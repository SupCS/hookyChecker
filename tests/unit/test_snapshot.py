from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from hooky_checker.db.models import Alert, AlertStatus, Base, DataSource, RawSnapshot
from hooky_checker.pipeline.snapshot import dataframe_from_values, publish_push_snapshot


def test_dataframe_from_values_uses_first_row_as_headers() -> None:
    frame = dataframe_from_values([["Date", "Revenue"], ["2026-07-01", 12]])
    assert frame.to_dict(orient="records") == [{"Date": "2026-07-01", "Revenue": 12}]


def test_snapshot_is_idempotent_for_same_payload() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = DataSource(name="Test", worksheet_name="All_Data")
        session.add(source)
        session.flush()
        values = [["Date", "Revenue"], ["2026-07-01", 12]]
        first, first_created = publish_push_snapshot(
            session, source.id, values, date(2026, 7, 9)
        )
        second, second_created = publish_push_snapshot(
            session, source.id, values, date(2026, 7, 9)
        )
        session.commit()

        assert first.id == second.id
        assert first_created is True
        assert second_created is False
        assert session.query(RawSnapshot).count() == 1


def test_missing_date_creates_alert_and_normal_data_starts_recovery() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = DataSource(name="Alerts", worksheet_name="All_Data")
        session.add(source)
        session.flush()
        normal = [
            ["Date", "Channel_Short_Name", "Conversions", "Revenue"],
            ["2026-07-01", "Search", 100, 1000],
            ["2026-07-02", "Search", 80, 800],
        ]
        broken = [
            ["Date", "Channel_Short_Name", "Conversions", "Revenue"],
            ["2026-07-02", "Search", 80, 800],
        ]
        publish_push_snapshot(session, source.id, normal, date(2026, 7, 9))
        publish_push_snapshot(session, source.id, broken, date(2026, 7, 9))
        alert = session.scalar(select(Alert).where(Alert.check_type == "missing_date"))
        assert alert is not None
        assert alert.status == AlertStatus.OPEN

        publish_push_snapshot(session, source.id, normal, date(2026, 7, 10))
        session.flush()
        assert alert.status == AlertStatus.RECOVERED
