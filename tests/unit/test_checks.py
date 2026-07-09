import pandas as pd

from hooky_checker.checks import find_metric_drops, find_missing_dates


def test_finds_missing_date() -> None:
    previous = pd.DataFrame({"data_date": ["2026-06-14", "2026-06-15"]})
    current = pd.DataFrame({"data_date": ["2026-06-15"]})
    results = find_missing_dates(previous, current)
    assert len(results) == 1
    assert results[0].dimensions == {"data_date": "2026-06-14"}


def test_finds_large_metric_drop() -> None:
    previous = pd.DataFrame(
        {"data_date": ["2026-06-15"], "channel": ["Search"], "conversions": [100]}
    )
    current = pd.DataFrame(
        {"data_date": ["2026-06-15"], "channel": ["Search"], "conversions": [60]}
    )
    results = find_metric_drops(
        previous,
        current,
        ["data_date", "channel"],
        "conversions",
        relative_threshold=0.2,
        absolute_threshold=10,
    )
    assert len(results) == 1
    assert results[0].actual == 60
