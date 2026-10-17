import datetime as dt

import streamlit as st

from database import db, repository as repo


def render():
    st.title("Settings")
    s = db.get_settings()
    with st.form("settings"):
        st.subheader("Database")
        sd = st.date_input("Database start date", dt.date.fromisoformat(s["data_start_date"]), format="DD/MM/YYYY",
                           help="First day of your real data. Uploads for earlier dates are refused.")
        st.subheader("Department")
        c = st.columns(3)
        mc = c[0].text_input("Number of machines", s["machine_count"], help="Used only for 'NPT % of scheduled time'. Leave empty to hide that KPI.")
        sched = c[1].number_input("Scheduled time per machine per day (h)", 1.0, 24.0, float(s["scheduled_hours"]), 0.5, help="2 shifts x 12 h")
        sa = c[2].text_input("Shift A start hour (0-23)", s["shift_a_start"], help="Example: 8 means A = 08:00-20:00, B = 20:00-08:00. Leave empty to turn shift split off.")
        st.subheader("Data checks")
        longnpt = st.number_input("Warn for one NPT event longer than (h)", 1.0, 240.0, float(s["long_npt_hours"]), 1.0)
        st.subheader("Optional goals (shown as a dashed line on daily charts)")
        c = st.columns(2)
        ng = c[0].text_input("NPT goal (hours per day)", s["npt_goal_h_per_day"])
        rg = c[1].text_input("Rejection goal (pieces per day)", s["rejection_goal_pieces_per_day"])
        st.subheader("Rejection report units")
        c = st.columns(2)
        qf = c[0].number_input("Quantity x this = pieces", 0.0001, 1e6, float(s["rejection_qty_to_pieces"]), help="Report Quantity is in thousand pieces")
        wf = c[1].number_input("Weight x this = kg", 0.0001, 1e6, float(s["rejection_weight_to_kg"]), help="Report Weight is in tons")
        st.caption("Unit changes apply to files uploaded after saving. Stored rejection records keep the values they were saved with.")
        if st.form_submit_button("Save settings", type="primary"):
            bad = [n for n, v in (("Number of machines", mc), ("Shift A start hour", sa), ("NPT goal", ng), ("Rejection goal", rg)) if v.strip() and not _num(v)]
            if bad or (sa.strip() and not 0 <= float(sa) <= 23):
                st.error("Please enter numbers only" + (f" ({', '.join(bad)})" if bad else " (shift hour must be 0-23)") + ".")
            else:
                for k, v in dict(data_start_date=sd.isoformat(), machine_count=mc.strip(), scheduled_hours=sched, shift_a_start=sa.strip(), long_npt_hours=longnpt,
                                 npt_goal_h_per_day=ng.strip(), rejection_goal_pieces_per_day=rg.strip(),
                                 rejection_qty_to_pieces=qf, rejection_weight_to_kg=wf).items():
                    db.set_setting(k, v)
                st.success("Saved.")
    _manage()
    st.caption("Machines are created automatically from the reports (IMM-380-03 and IMM-380-3 are the same machine). "
               "NPT and rejection causes are read from the reports, so new causes appear automatically.")


def _num(v):
    try:
        float(v)
        return True
    except ValueError:
        return False


def _manage():
    with st.expander("Manage stored data (delete a day / reset)"):
        st.warning("Deleting cannot be undone. Upload history is kept. Use Excel Export & Backup first if unsure.")
        c = st.columns(3)
        kind = c[0].selectbox("Report type", ["NPT", "Rejection"], key="del_kind")
        day = c[1].date_input("Date to delete", format="DD/MM/YYYY", key="del_day")
        sure = c[2].checkbox("I confirm the delete", key="del_ok")
        if st.button("Delete this day's data", disabled=not sure):
            n = repo.delete_day(kind, day)
            st.success(f"{n} record(s) removed for {kind} on {day:%d-%b-%Y}.")
        st.divider()
        st.write("**Reset the database** - removes ALL NPT and Rejection data. A backup is made first - "
                 "download it before you close this page, since it is not saved anywhere else.")
        word = st.text_input("Type RESET to unlock", key="reset_word")
        if st.button("Reset database", disabled=word != "RESET"):
            data, name = repo.reset_all()
            st.session_state["_reset_backup"] = (data, name)
            st.success("Database emptied.")
        if "_reset_backup" in st.session_state:
            data, name = st.session_state["_reset_backup"]
            st.download_button("Download the backup made just before the reset", data, file_name=name, mime="application/gzip")
