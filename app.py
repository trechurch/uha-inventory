"""
Inventory Management System - Streamlit Web App
Runs from anywhere via Streamlit Community Cloud
"""

import streamlit as st
import pandas as pd
import io
import tempfile
import os
from datetime import datetime
from typing import Optional

# ── Page config (must be first Streamlit call) ──────────────────────
st.set_page_config(
    page_title="UHA Inventory",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Module imports ───────────────────────────────────────────────────
from database import InventoryDatabase
from importer import InventoryImporter
from gl_manager import GLCodeManager
import onedrive_connector as od


# ────────────────────────────────────────────────────────────────────
# SESSION STATE HELPERS
# ────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_db():
    return InventoryDatabase()


def get_importer():
    return InventoryImporter(get_db())


def get_gl():
    return GLCodeManager(get_db())


def _init_import_state():
    if "import_data"      not in st.session_state:
        st.session_state.import_data      = []
    if "import_committed" not in st.session_state:
        st.session_state.import_committed = False
    if "import_results"   not in st.session_state:
        st.session_state.import_results   = {}


# ────────────────────────────────────────────────────────────────────
# ONEDRIVE AUTH SIDEBAR
# ────────────────────────────────────────────────────────────────────
def onedrive_auth_sidebar():
    with st.sidebar:
        st.markdown("---")
        st.caption("☁️ OneDrive integration pending IT approval")


# ────────────────────────────────────────────────────────────────────
# PAGES
# ────────────────────────────────────────────────────────────────────

def page_dashboard():
    st.title("🏟️ UHA Inventory — Dashboard")
    db = get_db()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Items",  db.count_items("active"))
    c2.metric("Total Value",  f"${db.get_inventory_value():,.2f}")
    c3.metric("Low Stock",    len(db.get_low_stock_items()))
    c4.metric("Last Updated", datetime.now().strftime("%m/%d/%Y"))

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🔴 Low Stock Items")
        low = db.get_low_stock_items()
        if low:
            df = pd.DataFrame(low)[[
                "description", "pack_type", "quantity_on_hand", "reorder_point", "vendor"
            ]]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.success("All items are stocked above reorder points.")

    with col2:
        st.subheader("📋 Recently Updated")
        items = db.get_all_items()
        if items:
            df = pd.DataFrame(items)
            if "last_updated" in df.columns:
                df = df.sort_values("last_updated", ascending=False).head(15)
            cols = [c for c in [
                "description", "pack_type", "cost", "vendor", "last_updated", "status_tag"
            ] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)


def page_inventory():
    st.title("📦 Inventory Items")
    db = get_db()

    all_items = db.get_all_items("active")

    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        search = st.text_input("🔍 Search", placeholder="item name, vendor, GL code...")
    with col2:
        gl_options = sorted(set(
            i.get("gl_code") for i in all_items if i.get("gl_code")
        ))
        gl_filter = st.multiselect(
            "GL Code filter",
            options=gl_options,
            placeholder="All GL codes",
        )
    with col3:
        show_disc = st.checkbox("Show discontinued")

    if search:
        items = db.search_items(search)
        if not show_disc:
            items = [i for i in items if i.get("record_status") == "active"]
    elif show_disc:
        items = db.get_all_items(None)
    else:
        items = all_items

    if gl_filter:
        items = [i for i in items if i.get("gl_code") in gl_filter]

    if not items:
        st.info("No items found.")
        return

    df = pd.DataFrame(items)
    display_cols = [c for c in [
        "description", "pack_type", "cost", "per", "vendor",
        "gl_code", "gl_name", "status_tag", "quantity_on_hand",
        "is_chargeable", "cost_center"
    ] if c in df.columns]

    st.caption(f"{len(df)} items")
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("✏️ Edit Item")

    keys = [i["key"] for i in items]
    selected_key = st.selectbox("Select item to edit", keys,
                                format_func=lambda k: k.split("||")[0])
    if selected_key:
        item = db.get_item(selected_key)
        if item:
            _edit_item_form(db, item)


def _edit_item_form(db, item: dict):
    with st.form("edit_item"):
        col1, col2, col3 = st.columns(3)

        with col1:
            desc      = st.text_input("Description", value=item.get("description", ""))
            pack_type = st.text_input("Pack Type",   value=item.get("pack_type", ""))
            cost      = st.number_input("Cost", value=float(item.get("cost") or 0), format="%.4f")
            per       = st.text_input("Per",         value=item.get("per", "") or "")

        with col2:
            vendor      = st.text_input("Vendor",      value=item.get("vendor", "") or "")
            item_number = st.text_input("Item #",      value=item.get("item_number", "") or "")
            gl_code     = st.text_input("GL Code",     value=item.get("gl_code", "") or "")
            gl_name     = st.text_input("GL Name",     value=item.get("gl_name", "") or "")

        with col3:
            yield_val  = st.number_input("Yield",       value=float(item.get("yield") or 1.0), format="%.4f")
            conv_ratio = st.number_input("Conv. Ratio", value=float(item.get("conv_ratio") or 1.0), format="%.4f")
            qoh        = st.number_input("Qty on Hand", value=float(item.get("quantity_on_hand") or 0), format="%.2f")
            notes      = st.text_area("Notes",          value=item.get("user_notes", "") or "")

        st.markdown("**Override Locks** — checked = this field won't be changed by imports")
        oc1, oc2, oc3 = st.columns(3)
        lock_pack  = oc1.checkbox("Lock Pack Type",   value=bool(item.get("override_pack_type")))
        lock_yield = oc2.checkbox("Lock Yield",       value=bool(item.get("override_yield")))
        lock_conv  = oc3.checkbox("Lock Conv. Ratio", value=bool(item.get("override_conv_ratio")))

        submitted = st.form_submit_button("💾 Save Changes")

    if submitted:
        updates = {
            "description":      desc.strip().upper(),
            "pack_type":        pack_type.strip().upper(),
            "cost":             cost,
            "per":              per,
            "vendor":           vendor,
            "item_number":      item_number,
            "gl_code":          gl_code,
            "gl_name":          gl_name,
            "yield":            yield_val,
            "conv_ratio":       conv_ratio,
            "quantity_on_hand": qoh,
            "user_notes":       notes,
            "last_updated":     datetime.utcnow(),
        }
        if lock_pack:
            updates["override_pack_type"] = pack_type.strip().upper()
        elif not lock_pack and item.get("override_pack_type"):
            updates["override_pack_type"] = None

        if lock_yield:
            updates["override_yield"] = yield_val
        elif not lock_yield and item.get("override_yield"):
            updates["override_yield"] = None

        if lock_conv:
            updates["override_conv_ratio"] = conv_ratio
        elif not lock_conv and item.get("override_conv_ratio"):
            updates["override_conv_ratio"] = None

        db._apply_update(item["key"], updates,
                         change_source="manual_edit", changed_by="user")
        st.success("✅ Saved!")
        st.rerun()


# ────────────────────────────────────────────────────────────────────
# IMPORT PAGE — checkbox state helpers
# ────────────────────────────────────────────────────────────────────

def _ck(filename: str, item_key: str) -> str:
    """Unique session_state key for an individual item checkbox."""
    return f"chk||{filename}||{item_key}"


def _all_items_for_file(d: dict) -> list:
    if not d.get("analysis"):
        return []
    return d["analysis"]["new_items"] + d["analysis"]["updates"]


def _file_selection_state(d: dict):
    """
    Returns: True  = all checked
             False = none checked
             None  = some checked (indeterminate)
    """
    items = _all_items_for_file(d)
    if not items:
        return False
    checked = [st.session_state.get(_ck(d["filename"], i["key"]), True) for i in items]
    if all(checked):
        return True
    if not any(checked):
        return False
    return None  # indeterminate


def _global_selection_state() -> object:
    """
    Returns: True  = all files fully checked
             False = nothing checked anywhere
             None  = mixed (indeterminate)
    """
    states = [_file_selection_state(d) for d in st.session_state.import_data if d.get("analysis")]
    if not states:
        return False
    if all(s is True for s in states):
        return True
    if all(s is False for s in states):
        return False
    return None


def _set_file_items(d: dict, value: bool):
    for item in _all_items_for_file(d):
        st.session_state[_ck(d["filename"], item["key"])] = value


def _set_all_items(value: bool):
    for d in st.session_state.import_data:
        _set_file_items(d, value)


def _toggle_icon(state) -> str:
    """
    True        → ☑  (all selected)
    False       → ☐  (none selected)
    None        → ▣  (some selected — indeterminate)
    """
    if state is True:
        return "☑"
    if state is False:
        return "☐"
    return "▣"


def _count_selected() -> int:
    total = 0
    for d in st.session_state.import_data:
        for item in _all_items_for_file(d):
            if st.session_state.get(_ck(d["filename"], item["key"]), True):
                total += 1
    return total


def _analyze_uploaded_files(uploaded_files, importer):
    st.session_state.import_data      = []
    st.session_state.import_committed = False
    st.session_state.import_results   = {}

    for f in uploaded_files:
        content        = f.read()
        parse_warnings = []
        analysis       = None
        suffix         = ".csv" if f.name.lower().endswith(".csv") else ".xlsx"

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            df_read = importer.read_file(tmp_path)
            os.unlink(tmp_path)

            if df_read is None:
                parse_warnings.append(f"Read error: {'; '.join(importer.errors)}")
            else:
                analysis = importer.analyze_import(df_read)
                parse_warnings.extend(analysis.get("errors", []))

        except Exception as e:
            parse_warnings.append(str(e))
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        entry = {
            "filename":       f.name,
            "size":           f.size,
            "content":        content,
            "analysis":       analysis,
            "parse_warnings": parse_warnings,
        }
        st.session_state.import_data.append(entry)

        # Pre-populate checkboxes — default all True (selected)
        if analysis:
            for item in _all_items_for_file(entry):
                ck = _ck(f.name, item["key"])
                if ck not in st.session_state:
                    st.session_state[ck] = True


def _execute_selected_imports(importer):
    results = {
        "files_processed": 0,
        "new_items_added": 0,
        "items_updated":   0,
        "errors":          [],
    }
    doc_date = datetime.now().strftime("%Y-%m-%d")

    for d in st.session_state.import_data:
        analysis = d["analysis"]
        if not analysis:
            continue
        filename = d["filename"]

        selected_new = [
            i for i in analysis["new_items"]
            if st.session_state.get(_ck(filename, i["key"]), True)
        ]
        selected_upd = [
            i for i in analysis["updates"]
            if st.session_state.get(_ck(filename, i["key"]), True)
        ]
        if not selected_new and not selected_upd:
            continue

        filtered = {
            "new_items": selected_new,
            "updates":   selected_upd,
            "skipped":   analysis["skipped"],
            "errors":    analysis["errors"],
        }
        r = importer.execute_import(
            filtered,
            changed_by="web_import",
            source_document=filename,
            doc_date=doc_date,
        )
        results["new_items_added"] += r.get("new_items_added", 0)
        results["items_updated"]   += r.get("items_updated",   0)
        results["errors"].extend(r.get("errors", []))
        results["files_processed"] += 1

        if od.get_access_token():
            od.archive_file(filename, d["content"])

    st.session_state.import_results   = results
    st.session_state.import_committed = True
    st.rerun()


# ────────────────────────────────────────────────────────────────────
# IMPORT REVIEW UI
# ────────────────────────────────────────────────────────────────────

def _render_select_all_toggle(state, button_key: str, label_suffix: str = "") -> bool:
    """
    Renders a single toggle button that shows ☐ / ☑ / ▣.
    Returns True if clicked.
    """
    icon  = _toggle_icon(state)
    label = f"{icon}  Select All{' ' + label_suffix if label_suffix else ''}"
    return st.button(label, key=button_key)


def _render_import_review(importer):
    data          = st.session_state.import_data
    total_files   = len(data)
    total_new     = sum(len(d["analysis"]["new_items"]) for d in data if d["analysis"])
    total_upd     = sum(len(d["analysis"]["updates"])   for d in data if d["analysis"])
    total_skip    = sum(
        len(d["analysis"]["skipped"]) + len(d["analysis"]["errors"])
        for d in data if d["analysis"]
    )
    total_warn    = sum(len(d["parse_warnings"]) for d in data)
    selected_now  = _count_selected()
    global_state  = _global_selection_state()

    # ── Top summary metrics ──────────────────────────────────────────
    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    mc1.metric("Files",        total_files)
    mc2.metric("🆕 New Items", total_new)
    mc3.metric("🔄 Updates",   total_upd)
    mc4.metric("⏭️ Skipped",   total_skip)
    mc5.metric("✅ Selected",  selected_now)

    if total_warn:
        st.warning(f"⚠️ {total_warn} parsing issue(s) detected — see per-file details below.")

    st.markdown("---")

    # ── Global Select All + top Commit ──────────────────────────────
    gh1, gh2 = st.columns([3, 3])

    with gh1:
        # Toggle: if all selected → clicking deselects all; otherwise → selects all
        if _render_select_all_toggle(global_state, "glob_sel_top",
                                     f"({total_new + total_upd} items across {total_files} files)"):
            _set_all_items(global_state is not True)   # True→deselect, False/None→select
            st.rerun()

    with gh2:
        commit_label = f"✅ Commit {selected_now} change{'s' if selected_now != 1 else ''}"
        if st.button(commit_label, key="commit_top", type="primary",
                     disabled=(selected_now == 0)):
            _execute_selected_imports(importer)

    st.markdown("---")

    # ── Per-file sections ────────────────────────────────────────────
    for d in data:
        filename   = d["filename"]
        analysis   = d["analysis"]
        warnings   = d["parse_warnings"]
        size_str   = (
            f"{d['size'] / 1024:.1f} KB"
            if d["size"] < 1_048_576
            else f"{d['size'] / 1_048_576:.1f} MB"
        )
        file_state = _file_selection_state(d)
        file_items = _all_items_for_file(d)
        file_sel   = sum(
            1 for i in file_items
            if st.session_state.get(_ck(filename, i["key"]), True)
        )
        file_new   = len(analysis["new_items"]) if analysis else 0
        file_upd   = len(analysis["updates"])   if analysis else 0

        # File header row
        fh1, fh2, fh3, fh4 = st.columns([4, 2, 2, 3])
        fh1.markdown(f"**📄 {filename}** &nbsp; `{size_str}`")
        fh2.caption(f"🆕 {file_new} new")
        fh3.caption(f"🔄 {file_upd} updates")

        if analysis is None:
            fh4.error("❌ Failed to parse")
            for w in warnings:
                st.caption(f"&nbsp;&nbsp;⚠️ {w}")
            st.markdown("---")
            continue

        if warnings:
            fh4.warning(f"⚠️ {len(warnings)} parsing issue(s)")
            with st.expander("Show parsing issues"):
                for w in warnings:
                    st.caption(w)
        else:
            fh4.success("✅ No parsing issues")

        # File-level Select All toggle
        fs1, fs2 = st.columns([3, 5])
        with fs1:
            safe_key = filename.replace(" ", "_").replace(".", "_")
            if _render_select_all_toggle(file_state, f"fsel_{safe_key}",
                                         f"({file_sel}/{len(file_items)})"):
                _set_file_items(d, file_state is not True)
                st.rerun()

        # Individual items in expander
        with st.expander(
            f"Items — {file_sel} of {len(file_items)} selected",
            expanded=True,
        ):
            for item in file_items:
                ck      = _ck(filename, item["key"])
                is_new  = item in analysis["new_items"]
                tag     = "🆕" if is_new else "🔄"

                change_note = ""
                if not is_new and item.get("changes"):
                    parts = [
                        f"{field}: {vals.get('old', '')} → {vals.get('new', '')}"
                        for field, vals in item["changes"].items()
                    ]
                    change_note = f"  ·  *{',  '.join(parts)}*"

                label = f"{tag} **{item['description']}** `{item['key'].split('||')[1]}`{change_note}"
                # Use the session_state value directly as the checkbox value
                current_val = st.session_state.get(ck, True)
                new_val = st.checkbox(label, value=current_val, key=ck)
                # If individual item changed, Streamlit updates session_state automatically via key=

        st.markdown("---")

    # ── Bottom commit bar ────────────────────────────────────────────
    selected_bot  = _count_selected()
    global_state2 = _global_selection_state()

    bc1, bc2 = st.columns([3, 3])
    with bc1:
        if _render_select_all_toggle(global_state2, "glob_sel_bot",
                                     f"({total_new + total_upd} items across {total_files} files)"):
            _set_all_items(global_state2 is not True)
            st.rerun()
    with bc2:
        commit_label2 = f"✅ Commit {selected_bot} change{'s' if selected_bot != 1 else ''}"
        if st.button(commit_label2, key="commit_bot", type="primary",
                     disabled=(selected_bot == 0)):
            _execute_selected_imports(importer)


def _render_import_results():
    r = st.session_state.import_results
    st.success("✅ Import committed successfully!")

    rc1, rc2, rc3 = st.columns(3)
    rc1.metric("Files Processed", r.get("files_processed", 0))
    rc2.metric("New Items Added",  r.get("new_items_added", 0))
    rc3.metric("Items Updated",    r.get("items_updated",   0))

    if r.get("errors"):
        with st.expander(f"⚠️ {len(r['errors'])} error(s)"):
            for e in r["errors"]:
                st.caption(e)

    st.markdown("---")
    if st.button("📥 Import More Files"):
        st.session_state.import_data      = []
        st.session_state.import_committed = False
        st.session_state.import_results   = {}
        st.rerun()


# ────────────────────────────────────────────────────────────────────
# IMPORT PAGE
# ────────────────────────────────────────────────────────────────────

def page_import():
    st.title("📥 Import Files")
    _init_import_state()
    importer = get_importer()

    tab1, tab2 = st.tabs(["📤 Upload from Computer", "☁️ Import from OneDrive"])

    with tab1:
        st.subheader("Upload Invoice or Inventory CSV / XLSX")
        uploaded = st.file_uploader(
            "Drop vendor invoice CSV or PAC export here",
            type=["csv", "xlsx"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )

        if uploaded:
            # Show uploaded files in a compact 3-column grid — no pagination
            st.markdown("**Uploaded files:**")
            grid_cols = st.columns(3)
            for i, f in enumerate(uploaded):
                size_str = (
                    f"{f.size / 1024:.1f} KB"
                    if f.size < 1_048_576
                    else f"{f.size / 1_048_576:.1f} MB"
                )
                grid_cols[i % 3].markdown(f"📄 `{f.name}` &nbsp; {size_str}")

            st.markdown("---")

            uploaded_names = [f.name for f in uploaded]
            existing_names = [d["filename"] for d in st.session_state.import_data]

            if uploaded_names != existing_names:
                with st.spinner(f"Analyzing {len(uploaded)} file(s)..."):
                    _analyze_uploaded_files(uploaded, importer)

            if st.session_state.import_committed:
                _render_import_results()
            elif st.session_state.import_data:
                _render_import_review(importer)

        else:
            if st.session_state.import_data:
                st.session_state.import_data      = []
                st.session_state.import_committed = False
                st.session_state.import_results   = {}

    with tab2:
        if not od.get_access_token():
            st.warning("Connect to OneDrive first (sidebar).")
        else:
            st.subheader("Files waiting in OneDrive Imports folder")
            files = od.list_import_files()
            if not files:
                st.info("No files found in your OneDrive Imports folder.")
            else:
                for f in files:
                    col1, col2, col3 = st.columns([4, 2, 1])
                    col1.write(f["name"])
                    col2.write(f.get("modified", "")[:10])
                    if col3.button("Import", key=f"od_{f['name']}"):
                        content = od.download_import_file(f["name"])
                        if content:
                            st.info(f"Processing {f['name']}...")
                            from pathlib import Path
                            suffix = Path(f["name"]).suffix
                            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                                tmp.write(content)
                                tmp_path = tmp.name
                            analysis, results = importer.import_file(
                                tmp_path, changed_by="onedrive_import"
                            )
                            os.unlink(tmp_path)
                            st.success(
                                f"Done: {results.get('new_items_added', 0)} added, "
                                f"{results.get('items_updated', 0)} updated."
                            )
                            od.archive_file(f["name"], content)


def page_gl_codes():
    st.title("🏷️ GL Code Manager")
    db = get_db()
    gl = get_gl()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Load GL Lists from OneDrive")
        if not od.get_access_token():
            st.warning("Connect OneDrive to load GL lists automatically.")
        else:
            if st.button("🔄 Reload GL Lists from OneDrive"):
                with st.spinner("Loading..."):
                    entries = od.load_gl_files_from_onedrive()
                    for gl_code, gl_name, desc in entries:
                        gl.add_gl_mapping(gl_code, gl_name, desc)
                st.success(f"Loaded {len(entries):,} GL entries.")

        st.subheader("Auto-Assign GL Codes")
        confidence = st.slider("Minimum confidence", 0.5, 0.95, 0.70)
        if st.button("🤖 Auto-Assign to Unassigned Items"):
            with st.spinner("Matching..."):
                results = gl.assign_gl_codes_to_items(min_confidence=confidence)
            st.success(
                f"Assigned: {results['assigned']} | "
                f"Skipped: {results['skipped']} | "
                f"Failed: {results['failed']}"
            )
            if results["assignments"]:
                adf = pd.DataFrame(results["assignments"])[[
                    "description", "gl_code", "gl_name", "confidence"
                ]]
                st.dataframe(adf, use_container_width=True, hide_index=True)

    with col2:
        st.subheader("GL Code Summary")
        summary = gl.get_gl_summary()
        if summary:
            sdf = pd.DataFrame(summary)
            st.dataframe(sdf, use_container_width=True, hide_index=True)
        else:
            st.info("No GL mappings loaded yet.")


def page_history():
    st.title("📜 Change History")
    db = get_db()

    key_input = st.text_input("Enter item key or search term")
    if key_input:
        history = db.get_item_history(key_input)
        if not history:
            items = db.search_items(key_input)
            if items:
                keys     = [i["key"] for i in items]
                selected = st.selectbox("Select item", keys,
                                        format_func=lambda k: k.split("||")[0])
                history  = db.get_item_history(selected)

        if history:
            df   = pd.DataFrame(history)
            cols = [c for c in [
                "change_date", "change_type", "field_changed",
                "old_value", "new_value", "change_source",
                "changed_by", "source_document"
            ] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
        else:
            st.info("No history found.")


def page_export():
    st.title("📤 Export")
    db = get_db()

    st.subheader("Export Full Inventory")
    items = db.get_all_items()
    if items:
        df = pd.DataFrame(items)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Inventory")
        buffer.seek(0)
        st.download_button(
            "⬇️ Download as Excel",
            data=buffer,
            file_name=f"inventory_export_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        csv = df.to_csv(index=False)
        st.download_button(
            "⬇️ Download as CSV",
            data=csv,
            file_name=f"inventory_export_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )
        st.caption(f"{len(df)} items")

    if od.get_access_token():
        st.subheader("Save to OneDrive")
        if st.button("☁️ Export to OneDrive"):
            buffer = io.BytesIO()
            df.to_excel(buffer, index=False)
            filename = f"inventory_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            od.archive_file(filename, buffer.getvalue(), subfolder="Exports")
            st.success(f"Saved {filename} to OneDrive Archives.")


# ────────────────────────────────────────────────────────────────────
# MAIN NAV
# ────────────────────────────────────────────────────────────────────
def main():
    with st.sidebar:
        st.image("https://img.icons8.com/emoji/96/stadium.png", width=60)
        st.title("UHA Inventory")
        st.markdown("---")
        page = st.radio("Navigate", [
            "🏠 Dashboard",
            "📦 Inventory",
            "📥 Import",
            "🏷️ GL Codes",
            "📜 History",
            "📤 Export",
        ])

    onedrive_auth_sidebar()

    if   page == "🏠 Dashboard": page_dashboard()
    elif page == "📦 Inventory": page_inventory()
    elif page == "📥 Import":    page_import()
    elif page == "🏷️ GL Codes":  page_gl_codes()
    elif page == "📜 History":   page_history()
    elif page == "📤 Export":    page_export()


if __name__ == "__main__":
    main()
