from datetime import datetime, timezone

from scripts.download_binance_usdm import parse_utc_ms


def test_parse_date_as_utc():
    expected = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert parse_utc_ms("2025-01-01") == expected


def test_parse_explicit_utc_timestamp():
    expected = int(datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert parse_utc_ms("2025-01-01T12:30:00Z") == expected