import pytest

from hooky_checker.adapters.google_sheets import build_csv_export_url


def test_build_csv_export_url() -> None:
    url = build_csv_export_url(
        "https://docs.google.com/spreadsheets/d/abc-123_X/edit#gid=42",
        "42",
    )
    assert url == (
        "https://docs.google.com/spreadsheets/d/abc-123_X/export?format=csv&gid=42"
    )


def test_rejects_invalid_sheet_url() -> None:
    with pytest.raises(ValueError):
        build_csv_export_url("https://example.com/not-a-sheet")
