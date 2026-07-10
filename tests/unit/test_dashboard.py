from datetime import date

from hooky_checker.dashboard import dashboard_payload, decimal_value


class Row:
    def __init__(self, payload, data_date=None):
        self.payload = payload
        self.data_date = data_date


def test_dashboard_payload_maps_dimensions_and_dynamic_metrics() -> None:
    rows = [
        Row(
            {
                "Date": "2026-07-01",
                "Campaign_Short_Name": "Summer",
                "Channel_Short_Name": "Search",
                "CNB_geo": "Austin",
                "Impressions": "1,000",
                "Cost": "$25.50",
                "Movie Tix": 3,
            },
            date(2026, 7, 1),
        )
    ]
    result = dashboard_payload(rows)
    assert result["warnings"] == []
    assert result["rows"][0]["campaign"] == "Summer"
    assert result["rows"][0]["location"] == "Austin"
    assert result["rows"][0]["metrics"] == {
        "impressions": "1000",
        "cost": "25.50",
        "Movie Tix": "3",
    }


def test_decimal_value_rejects_empty_and_non_numeric_values() -> None:
    assert decimal_value("") is None
    assert decimal_value("n/a") is None
    assert decimal_value("1.19%") is not None
