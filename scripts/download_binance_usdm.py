"""Download public Binance USDⓈ-M BTCUSDT 1m candles and funding history.

No API key is required. The Futures REST API is tried first; if it is region-blocked (HTTP 451),
the downloader falls back to Binance's public historical-data archive. Timestamps are UTC and
candle timestamps represent open times.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

BASE_URL = "https://fapi.binance.com"
VISION_URL = "https://data.binance.vision"
MINUTE_MS = 60_000
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def parse_utc_ms(value: str) -> int:
    """Parse an ISO-8601 timestamp/date as UTC milliseconds."""
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO timestamp: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _get_json(path: str, params: dict[str, str | int]) -> list:
    url = f"{BASE_URL}{path}?{urlencode(params)}"
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            request = Request(url, headers={"User-Agent": "btc-perp-bot-research/0.1"})
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and "code" in payload:
                raise RuntimeError(f"Binance API error: {payload}")
            if not isinstance(payload, list):
                raise RuntimeError(f"unexpected Binance API response: {payload}")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code == 451:
                raise
            if isinstance(exc, HTTPError) and exc.code not in (418, 429, 500, 502, 503, 504):
                raise
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"Binance request failed after retries: {last_error}")


def _month_starts(start_ms: int, end_ms: int):
    current = pd.Timestamp(start_ms, unit="ms", tz="UTC").normalize().replace(day=1)
    end = pd.Timestamp(end_ms, unit="ms", tz="UTC")
    while current < end:
        yield current
        current = current + pd.offsets.MonthBegin(1)


def _archive_csv(path: str) -> pd.DataFrame:
    url = f"{VISION_URL}/{path}"
    request = Request(url, headers={"User-Agent": "btc-perp-bot-research/0.1"})
    with urlopen(request, timeout=60) as response:
        payload = response.read()
    if path.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                raise RuntimeError(f"No CSV found in archive {url}")
            with archive.open(csv_names[0]) as source:
                return pd.read_csv(source, header=None)
    return pd.read_csv(io.BytesIO(payload), header=None)


def _drop_archive_header(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    first = str(frame.iloc[0, 0]).strip().lower()
    if first in {"open_time", "open time", "calc_time", "fundingtime", "funding_time", "timestamp"}:
        return frame.iloc[1:].reset_index(drop=True)
    return frame


def _normalise_kline_archive(frame: pd.DataFrame) -> pd.DataFrame:
    frame = _drop_archive_header(frame)
    if len(frame.columns) < 6:
        raise RuntimeError("Unexpected kline archive format")
    frame = frame.iloc[:, :6].copy()
    frame.columns = ["open_time", "open", "high", "low", "close", "volume"]
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="coerce")
    frame = frame.dropna(subset=["open_time"])
    return frame


def _download_klines_archive(start_ms: int, end_ms: int) -> pd.DataFrame:
    chunks = []
    for month in _month_starts(start_ms, end_ms):
        filename = f"BTCUSDT-1m-{month:%Y-%m}.zip"
        path = f"data/futures/um/monthly/klines/BTCUSDT/1m/{filename}"
        try:
            chunk = _normalise_kline_archive(_archive_csv(path))
        except HTTPError as exc:
            if exc.code == 404:
                print(f"Archive not found, skipping {filename}")
                continue
            raise
        chunk = chunk[(chunk["open_time"] >= start_ms) & (chunk["open_time"] < end_ms)]
        chunks.append(chunk)
        print(f"Downloaded archive {filename}: {len(chunk):,} candles")
    if not chunks:
        raise RuntimeError("No kline data returned by Binance API or historical archive")
    frame = pd.concat(chunks, ignore_index=True)
    frame["timestamp"] = pd.to_datetime(frame.pop("open_time").astype("int64"), unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = frame.set_index("timestamp").sort_index()
    return frame[~frame.index.duplicated(keep="last")][["open", "high", "low", "close", "volume"]]


def download_klines(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch 1m candles in [start_ms, end_ms), with a historical archive fallback."""
    rows: list[list] = []
    cursor = start_ms
    try:
        while cursor < end_ms:
            batch = _get_json("/fapi/v1/klines", {
                "symbol": "BTCUSDT", "interval": "1m", "startTime": cursor,
                "endTime": end_ms - 1, "limit": 1500,
            })
            if not batch:
                break
            rows.extend(row for row in batch if start_ms <= int(row[0]) < end_ms)
            next_cursor = int(batch[-1][0]) + MINUTE_MS
            if next_cursor <= cursor:
                raise RuntimeError("Binance kline pagination did not advance")
            cursor = next_cursor
            print(f"\rDownloaded {len(rows):,} 1m candles", end="", flush=True)
            if len(batch) < 1500:
                break
            time.sleep(0.15)
    except HTTPError as exc:
        if exc.code != 451:
            raise
        print("\nBinance Futures API returned HTTP 451; using public historical archives.")
        return _download_klines_archive(start_ms, end_ms)
    print()
    columns = ["open_time", "open", "high", "low", "close", "volume"]
    frame = pd.DataFrame([row[:6] for row in rows], columns=columns)
    if frame.empty:
        raise RuntimeError("No candles returned for the requested range")
    frame["timestamp"] = pd.to_datetime(frame.pop("open_time"), unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = frame.set_index("timestamp").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame[["open", "high", "low", "close", "volume"]]


def _download_funding_archive(start_ms: int, end_ms: int) -> pd.DataFrame:
    chunks = []
    for month in _month_starts(start_ms, end_ms):
        filename = f"BTCUSDT-fundingRate-{month:%Y-%m}.zip"
        path = f"data/futures/um/monthly/fundingRate/BTCUSDT/{filename}"
        try:
            frame = _drop_archive_header(_archive_csv(path))
        except HTTPError as exc:
            if exc.code == 404:
                print(f"Funding archive not found, skipping {filename}")
                continue
            raise
        if frame.empty or len(frame.columns) < 2:
            continue
        # Binance Vision funding archives have timestamp and rate columns; tolerate
        # optional extra columns by taking the first timestamp and final rate column.
        frame = frame.iloc[:, [0, -1]].copy()
        frame.columns = ["funding_time", "funding_rate"]
        frame["funding_time"] = pd.to_numeric(frame["funding_time"], errors="coerce")
        frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="coerce")
        frame = frame.dropna()
        chunks.append(frame)
    if not chunks:
        return pd.DataFrame(columns=["funding_rate"], index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))
    frame = pd.concat(chunks, ignore_index=True)
    frame = frame[(frame["funding_time"] >= start_ms) & (frame["funding_time"] < end_ms)]
    frame["timestamp"] = pd.to_datetime(frame["funding_time"].astype("int64"), unit="ms", utc=True)
    frame = frame.set_index("timestamp").sort_index()
    frame.index = frame.index.floor("min")
    return frame[["funding_rate"]].groupby(level=0).last()


def download_funding(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch funding rates in [start_ms, end_ms), with a historical archive fallback."""
    rows: list[dict] = []
    cursor = start_ms
    try:
        while cursor < end_ms:
            batch = _get_json("/fapi/v1/fundingRate", {
                "symbol": "BTCUSDT", "startTime": cursor, "endTime": end_ms - 1, "limit": 1000,
            })
            if not batch:
                break
            rows.extend(row for row in batch if start_ms <= int(row["fundingTime"]) < end_ms)
            next_cursor = int(batch[-1]["fundingTime"]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("Binance funding pagination did not advance")
            cursor = next_cursor
            if len(batch) < 1000:
                break
            time.sleep(0.15)
    except HTTPError as exc:
        if exc.code != 451:
            raise
        print("Binance funding API returned HTTP 451; using public historical archives.")
        return _download_funding_archive(start_ms, end_ms)
    if not rows:
        return pd.DataFrame(columns=["funding_rate"], index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["fundingTime"].astype("int64"), unit="ms", utc=True)
    frame["funding_rate"] = pd.to_numeric(frame["fundingRate"], errors="raise")
    frame = frame.set_index("timestamp").sort_index()
    frame.index = frame.index.floor("min")
    return frame[["funding_rate"]].groupby(level=0).last()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Binance USD-M BTCUSDT 1m OHLCV and funding rates")
    parser.add_argument("--start", required=True, help="UTC start, e.g. 2025-01-01 or 2025-01-01T00:00:00Z")
    parser.add_argument("--end", required=True, help="Exclusive UTC end timestamp/date")
    parser.add_argument("--output", default="data/btcusdt_perp_1m.csv")
    args = parser.parse_args()
    start_ms = parse_utc_ms(args.start)
    end_ms = parse_utc_ms(args.end)
    if start_ms >= end_ms:
        raise SystemExit("--start must be earlier than --end")
    bars = download_klines(start_ms, end_ms)
    funding = download_funding(start_ms, end_ms)
    bars["funding_rate"] = 0.0
    funding_count = 0
    if not funding.empty:
        matching = bars.index.intersection(funding.index)
        funding_count = len(matching)
        bars.loc[matching, "funding_rate"] = funding.loc[matching, "funding_rate"].to_numpy()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    bars.rename_axis("timestamp").to_csv(output, date_format="%Y-%m-%dT%H:%M:%SZ")
    print(f"Wrote {len(bars):,} candles to {output}")
    print(f"Funding settlements aligned to candles: {funding_count:,}")


if __name__ == "__main__":
    main()
