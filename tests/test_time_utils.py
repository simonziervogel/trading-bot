from datetime import datetime, timezone
import pytest

from kalshi.utils.time import to_naive_utc, utc_timestamp, parse_optional_dt, now_utc


def test_to_naive_iso():
    dt = to_naive_utc("2026-02-27T22:00:00Z")
    assert dt.tzinfo is None
    assert dt == datetime(2026, 2, 27, 22, 0, 0)


def test_to_naive_epoch():
    dt = to_naive_utc(1700000000)
    assert dt.tzinfo is None
    assert isinstance(dt, datetime)


def test_to_naive_aware_datetime():
    aware = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive = to_naive_utc(aware)
    assert naive.tzinfo is None
    assert naive == datetime(2026, 3, 15, 12, 0, 0)


def test_utc_timestamp_naive_and_aware_match():
    naive = datetime(2026, 2, 27, 22, 0, 0)
    aware = datetime(2026, 2, 27, 22, 0, 0, tzinfo=timezone.utc)
    assert utc_timestamp(naive) == utc_timestamp(aware)


def test_to_naive_none_raises():
    with pytest.raises(ValueError):
        to_naive_utc(None)


def test_parse_optional_dt_none():
    assert parse_optional_dt(None) is None
    assert parse_optional_dt("") is None


def test_now_utc_is_aware():
    t = now_utc()
    assert t.tzinfo is not None
