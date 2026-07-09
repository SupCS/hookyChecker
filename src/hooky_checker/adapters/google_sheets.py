import re
from dataclasses import dataclass

import pandas as pd

from hooky_checker.adapters.base import SourceAdapter

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def extract_sheet_id(sheet_url: str) -> str:
    match = _SHEET_ID_RE.search(sheet_url)
    if not match:
        raise ValueError("Не удалось извлечь Google Sheet ID из ссылки")
    return match.group(1)


def build_csv_export_url(sheet_url: str, worksheet_gid: str = "0") -> str:
    sheet_id = extract_sheet_id(sheet_url)
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
        f"?format=csv&gid={worksheet_gid}"
    )


@dataclass(frozen=True)
class GoogleSheetsPublicAdapter(SourceAdapter):
    """Reader for a Sheet published or shared for link-based reading."""

    sheet_url: str
    worksheet_gid: str = "0"

    def read(self) -> pd.DataFrame:
        frame = pd.read_csv(build_csv_export_url(self.sheet_url, self.worksheet_gid))
        frame.columns = [str(column).strip() for column in frame.columns]
        return frame.dropna(how="all").reset_index(drop=True)
