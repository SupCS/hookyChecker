from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

FIELD_CANDIDATES = {
    "date": ("Date", "date", "data_date"),
    "campaign": ("Campaign_Short_Name", "campaign_short_name"),
    "channel": ("Channel_Short_Name", "channel_short_name", "Channel", "channel"),
    "location": ("CNB_geo", "cnb_geo", "CNB_Geo", "Location", "location", "Geo", "geo"),
}
METRIC_CANDIDATES = {
    "impressions": ("Impressions", "impressions"),
    "clicks": ("Clicks", "clicks"),
    "cost": ("Cost", "cost", "Spend", "spend"),
    "conversions": ("Conversions", "conversions", "Total_Conversions"),
    "revenue": ("Revenue", "revenue"),
}
NON_ADDITIVE_COLUMNS = {
    "CTR",
    "ctr",
    "CPM",
    "cpm",
    "CPC",
    "cpc",
    "CPA",
    "cpa",
    "ROAS",
    "roas",
}


def first_present(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in columns), None)


def decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def dashboard_payload(rows: list[Any]) -> dict[str, Any]:
    payloads = [row.payload for row in rows]
    columns = {str(column) for payload in payloads for column in payload}
    fields = {
        name: first_present(columns, candidates) for name, candidates in FIELD_CANDIDATES.items()
    }
    metrics = {
        name: first_present(columns, candidates) for name, candidates in METRIC_CANDIDATES.items()
    }
    reserved = {
        column for column in (*fields.values(), *metrics.values()) if column
    } | NON_ADDITIVE_COLUMNS
    numeric_columns = sorted(
        column
        for column in columns - reserved
        if any(decimal_value(payload.get(column)) is not None for payload in payloads)
    )
    metric_columns = {**metrics, **{column: column for column in numeric_columns}}
    result_rows = []
    for row in rows:
        item: dict[str, Any] = {
            "date": row.data_date.isoformat() if row.data_date else None,
            "campaign": row.payload.get(fields["campaign"]) if fields["campaign"] else None,
            "channel": row.payload.get(fields["channel"]) if fields["channel"] else None,
            "location": row.payload.get(fields["location"]) if fields["location"] else None,
            "metrics": {},
        }
        if item["date"] is None and fields["date"]:
            value = row.payload.get(fields["date"])
            item["date"] = value.isoformat() if isinstance(value, date) else value
        for name, column in metric_columns.items():
            if column and (value := decimal_value(row.payload.get(column))) is not None:
                item["metrics"][name] = str(value)
        result_rows.append(item)
    return {
        "fields": fields,
        "metrics": list(metric_columns),
        "rows": result_rows,
        "warnings": [
            name for name in ("date", "campaign", "channel", "location") if not fields[name]
        ],
    }
