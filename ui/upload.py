import datetime as dt

import streamlit as st

from database import db, repository as repo
from processing.cleaner import FileValidationError
from processing.npt_processor import parse_npt
from processing.rejection_processor import parse_rejection
from .common import dataframe

TYPES = [("NPT", "NPT / Downtime Report"), ("Rejection", "Rejection Report")]
LABEL = dict(TYPES)


def _parse(kind, f, day, s, bulk=False, data_start=None):
    if kind == "NPT":
        return parse_npt(f, f.name, day, s, bulk=bulk, data_start=data_start)
    return parse_rejection(f, f.name, day, s)


def _rd_label(rep):
    """What to store in upload_history.report_date: one date normally, a readable range for bulk files."""
    if len(rep.dates) > 1:
        return f"{rep.dates[0].isoformat()} to {rep.dates[-1].isoformat()}"
    return rep.report_date.isoformat()


def _save_all(pending, choices):
    results = []
    for kind, rep in pending["reports"].items():
        info = pending["dups"][kind]
        choice = choices.get(kind, "Replace Existing Data") if info["existing"] else None
        rd = _rd_label(rep)
        if choice == "Cancel":
            repo.add_history(kind, rep.file_name, rd, rep.rows_read, 0, len(rep.rejected), "Cancelled", "Cancelled by user", 0)
            results.append((kind, "Cancelled - nothing saved."))
            continue
        mode = "new" if not info["existing"] else ("replace" if choice == "Replace Existing Data" else "skip")
        dup_status = {"new": "No duplicate", "replace": "Replaced", "skip": "Skipped duplicates"}[mode]
        uid = repo.add_history(kind, rep.file_name, rd, rep.rows_read, rep.rows_valid, len(rep.rejected),
                               "Success" if not rep.rejected else "Success with rejected rows", dup_status,
                               len(rep.rejected), "; ".join(rep.warnings)[:500])
        n = repo.save_report(rep, uid, mode)
        db.execute("UPDATE upload_history SET records_valid=? WHERE id=?", (n, uid))
        results.append((kind, f"Saved {n} record(s). {dup_status}."))
    return results


def render():
    st.title("Upload Reports")
    s = db.get_settings()
    start = dt.date.fromisoformat(s["data_start_date"])
    if db.DEMO:
        st.warning("PRACTICE MODE: this is a separate demo database. Do not use it for real data.")

    bulk = st.checkbox("NPT file covers a date range (bulk upload)", key="bulk_npt",
                       help="Use this when Smart Manufacturing exported 10, 15, or even 100+ days of NPT data "
                            "in one file. Each event is stored and replaced under its OWN day automatically - "
                            "no date needs to be picked, and historical days outside the file are never touched.")
    day = None
    if not bulk:
        day = st.date_input("Report Date", max(start, dt.date.today() - dt.timedelta(days=1)), min_value=start, format="DD/MM/YYYY",
                            help=f"The database starts on {start:%d-%b-%Y}. Earlier dates are not accepted (change this in Settings).")
    else:
        st.caption(f"Events dated before {start:%d-%b-%Y} (the database start date, set in Settings) are skipped "
                   "automatically and listed as rejected rows; nothing before it is ever stored.")

    files = {}
    cols = st.columns(2)
    npt_label = TYPES[0][1] + (" (date range)" if bulk else "")
    files["NPT"] = cols[0].file_uploader(npt_label, type=["xlsx", "xlsm"], key="up_NPT")
    if not bulk:
        files["Rejection"] = cols[1].file_uploader(TYPES[1][1], type=["xlsx", "xlsm"], key="up_Rejection")
    else:
        cols[1].caption("Rejection upload is single-day only; switch off bulk mode above to upload a Rejection file.")
        files["Rejection"] = None

    if st.button("PROCESS REPORTS", type="primary"):
        st.session_state.pop("last_results", None)
        if not any(files.values()):
            st.error("Please upload at least one file.")
            return
        if not bulk and day > dt.date.today():
            st.error("The report date is in the future.")
            return
        reports, errors = {}, {}
        with st.spinner("Reading and checking the files..."):
            for kind, _ in TYPES:
                f = files[kind]
                if f is None:
                    continue
                try:
                    reports[kind] = _parse(kind, f, day, s, bulk=(bulk and kind == "NPT"), data_start=start)
                except FileValidationError as e:
                    errors[kind] = str(e)
                except Exception as e:  # a bad file must never crash the page
                    errors[kind] = f"The file could not be read: {e}"
        for kind, msg in errors.items():
            errday = (day or start).isoformat()
            repo.add_history(kind, files[kind].name, errday, 0, 0, 0, "Failed", "-", 1, msg[:500])
        dups = {k: repo.existing_info(r) for k, r in reports.items()}
        st.session_state["pending"] = dict(reports=reports, errors=errors, dups=dups, date=day)
        if reports and not any(d["existing"] for d in dups.values()):
            st.session_state["last_results"] = _save_all(st.session_state["pending"], {})
            st.session_state["pending_view"] = st.session_state.pop("pending")
            st.session_state["pending_done"] = True
        else:
            st.session_state["pending_done"] = False

    pending = st.session_state.get("pending") or st.session_state.get("pending_view")
    if not pending:
        return
    done = st.session_state.get("pending_done", False)
    for kind, msg in pending["errors"].items():
        st.error(f"**{LABEL[kind]}:** {msg}")
    for kind, rep in pending["reports"].items():
        with st.expander(f"{LABEL[kind]} - {rep.rows_valid} valid row(s), {len(rep.rejected)} rejected row(s)", expanded=True):
            if len(rep.dates) > 1:
                st.write(f"Date range covered: {rep.dates[0]:%d-%b-%Y} to {rep.dates[-1]:%d-%b-%Y} "
                        f"({len(rep.dates)} day(s) with events) | rows read: {rep.rows_read} | "
                        f"valid: {rep.rows_valid} | rejected: {len(rep.rejected)}")
            else:
                st.write(f"Report date: {rep.report_date:%d-%b-%Y} | rows read: {rep.rows_read} | valid: {rep.rows_valid} | rejected: {len(rep.rejected)}")
            if rep.rejected:
                st.write("Rows not saved:")
                dataframe([{"Where": w, "Reason": r} for w, r in rep.rejected])
            for w in rep.warnings:
                st.warning(w)
            if rep.flags:
                st.write(f"{len(rep.flags)} data warning(s):")
                dataframe([{"Type": f["kind"], "Details": f["message"]} for f in rep.flags[:200]])
            info = pending["dups"][kind]
            if info["existing"] and not done:
                d_existing = info.get("dates_existing") or []
                if len(d_existing) > 1:
                    shown = ", ".join(d.strftime("%d-%b") if hasattr(d, "strftime") else str(d) for d in
                                      [dt.date.fromisoformat(x) for x in d_existing[:15]])
                    more = f" and {len(d_existing) - 15} more" if len(d_existing) > 15 else ""
                    st.warning(f"Data already exists for {len(d_existing)} of {len(rep.dates)} day(s) in this file: "
                              f"{shown}{more}. {info['detail']}")
                else:
                    st.warning(f"Data for this report/date already exists. {info['detail']}")

    if not done:
        need = {k: d for k, d in pending["dups"].items() if d["existing"]}
        choices = {}
        for kind in need:
            choices[kind] = st.radio(f"{LABEL[kind]} - what should be done?",
                                     ["Replace Existing Data", "Skip Duplicate Records", "Cancel"], key=f"choice_{kind}", horizontal=True)
        ok = True
        if any(c == "Replace Existing Data" for c in choices.values()):
            ok = st.checkbox("I confirm: existing records for this date will be replaced. Upload history is kept.", key="confirm_replace")
        if pending["reports"] and st.button("Save to database", type="primary", disabled=not ok):
            st.session_state["last_results"] = _save_all(pending, choices)
            st.session_state["pending_view"] = st.session_state.pop("pending")
            st.session_state["pending_done"] = True
            st.rerun()
    for kind, msg in st.session_state.get("last_results", []):
        st.success(f"{LABEL[kind]}: {msg}")
    if done and st.session_state.get("last_results"):
        st.info("Dashboards are updated. Open **Dashboard** or **Daily Analysis**.")
        nrep = pending["reports"].get("NPT")
        if nrep is not None and not nrep.df.empty:
            mapped = set(repo.get_machine_map().query("production_line.notna()", engine="python").machine_id) \
                if not repo.get_machine_map().empty else set()
            unmapped = sorted(set(nrep.df.machine_id) - mapped)
            if unmapped:
                shown = ", ".join(unmapped[:15]) + (f" and {len(unmapped) - 15} more" if len(unmapped) > 15 else "")
                st.warning(f"{len(unmapped)} machine(s) in this upload have no Machine No. / Production Line yet: "
                          f"{shown}. Map them on the **Machine Mapping** page so they show up correctly in reports.")
