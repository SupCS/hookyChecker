from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hooky_checker.db.models import Base, DataSource, RawSnapshot
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
