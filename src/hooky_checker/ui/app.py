import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from hooky_checker.config import get_settings
from hooky_checker.db.models import Alert, AlertStatus, DataSource, IngestionRun, RunStatus
from hooky_checker.db.session import create_schema, session_scope
from hooky_checker.security import generate_ingest_token, hash_ingest_token

st.set_page_config(page_title="Hooky Checker", page_icon="🪝", layout="wide")
create_schema()
settings = get_settings()

st.title("Hooky Checker")
st.caption("Google Sheets data quality monitoring")

overview_tab, sources_tab = st.tabs(["Monitoring", "Sources"])

with sources_tab:
    st.subheader("Connected projects")
    st.caption(
        "Google Sheet sends data through Apps Script. "
        "The sheet does not need to be published or connected to Google Cloud."
    )

    with st.form("add_source", clear_on_submit=True):
        source_name = st.text_input("Project name")
        worksheet_name = st.text_input("Worksheet name", value="All_Data")
        submitted = st.form_submit_button("Add source")
        if submitted:
            if not source_name.strip() or not worksheet_name.strip():
                st.error("Complete all fields")
            else:
                ingest_token = generate_ingest_token()
                try:
                    with session_scope() as session:
                        source = DataSource(
                            name=source_name.strip(),
                            worksheet_name=worksheet_name.strip(),
                            ingest_token_hash=hash_ingest_token(ingest_token),
                        )
                        session.add(source)
                        session.flush()
                        source_id = source.id
                    st.session_state["new_source_credentials"] = {
                        "source_id": source_id,
                        "token": ingest_token,
                    }
                    st.success(
                        "Source added. Copy the token below — "
                        "it will not be shown again."
                    )
                except IntegrityError:
                    st.error("A source with this name already exists")

    with session_scope() as session:
        sources = list(session.scalars(select(DataSource).order_by(DataSource.name)))

    if sources:
        selected_name = st.selectbox("Project", [source.name for source in sources])
        selected = next(source for source in sources if source.name == selected_name)
        credentials = st.session_state.get("new_source_credentials")
        if credentials and credentials["source_id"] == selected.id:
            st.markdown("Copy these values into `apps_script/Code.gs`:")
            st.code(
                f"API URL: {settings.public_api_url}\n"
                f"INGEST TOKEN: {credentials['token']}\n"
                f"WORKSHEET: {selected.worksheet_name}"
            )
        else:
            st.info(
                "The token is hidden. If it is lost, you will need to issue a new one "
                "(token rotation will be added in the next step)."
            )
    else:
        st.info("Add your first source above.")

with overview_tab:
    st.subheader("Monitoring status")

with session_scope() as session:
    latest_run = session.scalar(
        select(IngestionRun)
        .where(IngestionRun.status == RunStatus.SUCCESS)
        .order_by(IngestionRun.finished_at.desc())
        .limit(1)
    )
    active_count = session.scalar(
        select(func.count(Alert.id)).where(
            Alert.status.in_(
                [AlertStatus.OPEN, AlertStatus.ONGOING, AlertStatus.RECOVERED]
            )
        )
    )
    critical_count = session.scalar(
        select(func.count(Alert.id)).where(
            Alert.status.in_([AlertStatus.OPEN, AlertStatus.ONGOING]),
            Alert.severity == "CRITICAL",
        )
    )
    alerts = list(session.scalars(select(Alert).order_by(Alert.last_seen_at.desc()).limit(200)))

with overview_tab:
    col1, col2, col3 = st.columns(3)
    col1.metric("Active alerts", active_count or 0)
    col2.metric("Critical", critical_count or 0)
    col3.metric(
        "Latest successful snapshot",
        latest_run.snapshot_date.isoformat() if latest_run else "not run yet",
    )

    st.subheader("Alerts")
    if not alerts:
        st.info("No alerts yet. Check results will appear here after connecting a sheet.")
    else:
        st.dataframe(
            [
                {
                    "status": alert.status.value,
                    "severity": alert.severity,
                    "check": alert.check_type,
                    "title": alert.title,
                    "dimensions": alert.dimensions,
                    "first_seen": alert.first_seen_at,
                    "last_seen": alert.last_seen_at,
                }
                for alert in alerts
            ],
            use_container_width=True,
            hide_index=True,
        )
