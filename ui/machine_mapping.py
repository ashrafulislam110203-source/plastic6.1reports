"""Machine ID -> Machine No. -> Production Line mapping.

Machines are created automatically (from the NPT/Rejection reports) the moment their ID is first
seen; this page is only for attaching a Machine No. and a Production Line to them. A machine with
no mapping yet works everywhere exactly as before - it just shows as "Unmapped" in reports until
it is mapped here, and nothing about NPT/Rejection storage depends on this page at all.
"""
import streamlit as st

from database import db, repository as repo
from processing.cleaner import FileValidationError
from processing.machine_map_processor import parse_machine_map
from utils.excel_export import build_workbook, notes_for
from .common import dataframe


def render():
    st.title("Machine Mapping")
    st.caption("Map each Machine ID to its Machine No. (position) and Production Line. "
              "This is used in reports, filters and exports - it never affects what NPT/Rejection data is stored.")

    cur = repo.get_machine_map()
    total = len(cur)
    mapped = int((cur.production_line.notna() & cur.machine_no.notna()).sum()) if total else 0
    c = st.columns(3)
    c[0].metric("Machines known", total)
    c[1].metric("Fully mapped", mapped)
    c[2].metric("Not yet mapped", total - mapped)
    if total == 0:
        st.info("No machines yet - upload an NPT or Rejection report first (machines are created from them "
               "automatically), or add mappings below and they will be matched up the moment the machine appears.")

    with st.expander("Upload a mapping file (Machine ID, Machine No., Production Line columns)", expanded=total == 0):
        st.caption("Any column order works, as long as the header row has these three column names "
                  "(or close variants, e.g. 'Machine No' or 'Line'). Excel (.xlsx) or CSV.")
        f = st.file_uploader("Mapping file", type=["xlsx", "xlsm", "csv"], key="map_file")
        if f is not None:
            try:
                rep = parse_machine_map(f, f.name)
            except FileValidationError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"The file could not be read: {e}")
            else:
                st.write(f"{rep.rows_valid} valid row(s), {len(rep.rejected)} rejected row(s)")
                if rep.rejected:
                    dataframe([{"Where": w, "Reason": r} for w, r in rep.rejected])
                for w in rep.warnings:
                    st.warning(w)
                if not rep.df.empty:
                    dataframe(rep.df.rename(columns={"machine_id": "Machine ID", "machine_no": "Machine No.",
                                                     "production_line": "Production Line"}))
                    if st.button(f"Save these {rep.rows_valid} mapping(s)", type="primary", key="map_save_upload"):
                        n = repo.import_machine_map(rep)
                        st.success(f"Saved {n} mapping(s).")
                        st.rerun()

    st.subheader("Edit mapping")
    only_unmapped = st.checkbox("Show only unmapped machines", key="map_only_unmapped")
    view = cur if not only_unmapped else cur[cur.machine_no.isna() | cur.production_line.isna()]
    if view.empty and not cur.empty:
        st.success("Every known machine is mapped.")
    elif not cur.empty:
        known_lines = repo.known_production_lines()
        st.caption("Lines seen so far (for reference - you can type any name, a new one is remembered automatically): "
                  + ", ".join(known_lines))
        show = view.rename(columns={"machine_id": "Machine ID", "size": "Size", "first_seen": "First Seen",
                                    "machine_no": "Machine No.", "production_line": "Production Line"})
        edited = st.data_editor(
            show, hide_index=True, key="map_editor", width="stretch",
            column_config={
                "Machine ID": st.column_config.TextColumn(disabled=True),
                "Size": st.column_config.TextColumn(disabled=True),
                "First Seen": st.column_config.TextColumn(disabled=True),
                "Machine No.": st.column_config.TextColumn(),
                "Production Line": st.column_config.TextColumn(),
            })
        if st.button("Save changes", type="primary", key="map_save_edit"):
            before = show.set_index("Machine ID")
            after = edited.set_index("Machine ID")
            changed = after[(after["Machine No."] != before["Machine No."]) |
                           (after["Production Line"] != before["Production Line"])]
            rows = [dict(machine_id=mid, machine_no=(r["Machine No."] or None), production_line=(r["Production Line"] or None))
                   for mid, r in changed.iterrows()]
            if rows:
                n = repo.set_machine_map(rows)
                st.success(f"Updated {n} machine(s).")
                st.rerun()
            else:
                st.info("Nothing changed.")

    if not cur.empty:
        st.download_button(
            "Export mapping to Excel",
            build_workbook({"Machine Mapping": cur.rename(columns={
                "machine_id": "Machine ID", "size": "Size", "first_seen": "First Seen",
                "machine_no": "Machine No.", "production_line": "Production Line"})}, None, notes_for(db.get_settings())),
            file_name="Machine_Mapping.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
