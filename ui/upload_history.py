import streamlit as st

from database import db
from .common import dataframe, table_button


def render():
    st.title("Upload History")
    db.init_db()
    df = db.query_df("SELECT upload_time, report_date, report_type, file_name, records_total, records_valid, records_rejected, "
                     "status, duplicate_status, error_count, message FROM upload_history ORDER BY id DESC")
    if df.empty:
        st.info("No uploads yet.")
        return
    df.columns = ["Upload Time", "Report Date", "Report Type", "File Name", "Records Read", "Records Saved",
                  "Rows Rejected", "Status", "Duplicate Status", "Errors", "Message"]
    dataframe(df)
    table_button("Export upload history to Excel", {"Upload History": df}, "Upload_History.xlsx", "uh_x")
