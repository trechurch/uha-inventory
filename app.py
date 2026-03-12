# ──────────────────────────────────────────────────────────────────────────────
#  app.py  —  UHA Inventory Management  —  Streamlit Web App
#  Runs from anywhere via Streamlit Community Cloud
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  STDLIB + THIRD-PARTY IMPORTS
# ──────────────────────────────────────────────────────────────────────────────

import re
import io
import time
import tempfile
import os
from datetime import datetime
from typing import Optional

import streamlit as st
import pandas as pd
import onedrive_connector as od

# ── end of stdlib + third-party imports ──────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  VERSION
# ──────────────────────────────────────────────────────────────────────────────

__version__ = "3.0.2"

# ── end of version ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE CONFIG  (must be the very first Streamlit call)
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="UHA Inventory",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── end of page config ────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  LOCAL MODULE IMPORTS
# ──────────────────────────────────────────────────────────────────────────────

from database import InventoryDatabase
from importer import InventoryImporter
from gl_manager import GLCodeManager
from count_importer import CountImporter, CountImportMeta
from status_bar import status_bar
from session_state import init_session_state, reset_db_mgmt_confirm
from ui_skeleton import (
    build_default_registry, MenuBar, SidebarConfig,
    DB_OPERATIONS, DEFAULT_MODE_REG
)

# ── end of local module imports ───────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  CACHED RESOURCE HELPERS
#  The version string is hashed from database.py's mtime so the cache busts
#  automatically whenever database.py is updated in the repo.
# ──────────────────────────────────────────────────────────────────────────────

import hashlib as _hashlib, pathlib as _pathlib

def _db_version() -> str:
    """Return a short hash of database.py so the cache key changes on update."""
    try:
        p = _pathlib.Path(__file__).parent / "database.py"
        return _hashlib.md5(p.read_bytes()).hexdigest()[:8]
    except Exception:
        return "0"

@st.cache_resource(hash_funcs={str: lambda s: s})
def get_db(_ver: str = ""):
    return InventoryDatabase()

def _get_db():
    """Always passes the current database.py hash so stale instances are evicted."""
    return get_db(_ver=_db_version())

def get_importer():
    return InventoryImporter(_get_db())

def get_gl():
    return GLCodeManager(_get_db())

def get_count_importer():
    return CountImporter(_get_db())

# ── end of cached resource helpers ───────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SESSION STATE INITIALIZERS
# ──────────────────────────────────────────────────────────────────────────────

def _init_import_state():
    if "import_data"        not in st.session_state:
        st.session_state.import_data        = []
    if "import_committed"   not in st.session_state:
        st.session_state.import_committed   = False
    if "import_results"     not in st.session_state:
        st.session_state.import_results     = {}
    if "import_selections"  not in st.session_state:
        st.session_state.import_selections  = {}  # {ck_string: bool}

# ── end of session state initializers ────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SIDEBAR
# ──────────────────────────────────────────────────────────────────────────────

def onedrive_auth_sidebar():
    with st.sidebar:
        st.markdown("---")
        st.caption("☁️ OneDrive integration pending IT approval")

# ── end of sidebar ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — DASHBOARD
# ──────────────────────────────────────────────────────────────────────────────

def page_dashboard():
    st.title("🏟️ UHA Inventory — Dashboard")
    db = _get_db()

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

# ── end of page — dashboard ───────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — INVENTORY LIST + EDIT FORM
# ──────────────────────────────────────────────────────────────────────────────

def page_inventory():
    st.title("📦 Inventory Items")
    db = _get_db()

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

# ── end of page — inventory list + edit form ─────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  IMPORT — CHECKBOX / SELECTION STATE HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _ck(filename: str, item_key: str) -> str:
    """Key string for import_selections dict entry."""
    return f"{filename}||{item_key}"


def _all_items_for_file(d: dict) -> list:
    if not d.get("analysis"):
        return []
    return d["analysis"]["new_items"] + d["analysis"]["updates"]


def _sel() -> dict:
    """Shortcut to the single source-of-truth selections dict."""
    return st.session_state.setdefault("import_selections", {})


def _get_sel(filename: str, item_key: str) -> bool:
    return _sel().get(_ck(filename, item_key), True)


def _set_sel(filename: str, item_key: str, value: bool):
    _sel()[_ck(filename, item_key)] = value


def _file_selection_state(d: dict):
    """True = all checked, False = none checked, None = indeterminate."""
    items = _all_items_for_file(d)
    if not items:
        return False
    checked = [_get_sel(d["filename"], i["key"]) for i in items]
    if all(checked):
        return True
    if not any(checked):
        return False
    return None


def _global_selection_state():
    """True = all files fully checked, False = nothing, None = mixed."""
    states = [
        _file_selection_state(d)
        for d in st.session_state.import_data
        if d.get("analysis")
    ]
    if not states:
        return False
    if all(s is True  for s in states):
        return True
    if all(s is False for s in states):
        return False
    return None


def _set_file_items(d: dict, value: bool):
    for item in _all_items_for_file(d):
        _set_sel(d["filename"], item["key"], value)


def _set_all_items(value: bool):
    for d in st.session_state.import_data:
        _set_file_items(d, value)


def _toggle_icon(state) -> str:
    """☑ = all selected  ▣ = some selected  ☐ = none selected."""
    if state is True:
        return "☑"
    if state is False:
        return "☐"
    return "▣"


def _count_selected() -> int:
    return sum(1 for v in _sel().values() if v)

# ── end of import — checkbox / selection state helpers ───────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  IMPORT — FILE ANALYSIS + EXECUTION
# ──────────────────────────────────────────────────────────────────────────────

def _analyze_uploaded_files(uploaded_files, importer):
    st.session_state.import_data       = []
    st.session_state.import_committed  = False
    st.session_state.import_results    = {}
    st.session_state.import_selections = {}   # full reset on new file list

    for f in uploaded_files:
        content        = f.read()
        parse_warnings = []
        analysis       = None
        tmp_path       = None
        suffix         = ".csv" if f.name.lower().endswith(".csv") else ".xlsx"

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            df_read = importer.read_file(tmp_path)
            os.unlink(tmp_path)
            tmp_path = None

            if df_read is None:
                parse_warnings.append(f"Read error: {'; '.join(importer.errors)}")
            else:
                analysis = importer.analyze_import(df_read)
                parse_warnings.extend(analysis.get("errors", []))

        except Exception as e:
            parse_warnings.append(str(e))
            if tmp_path:
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

        # Pre-populate all items as selected
        if analysis:
            for item in _all_items_for_file(entry):
                _set_sel(f.name, item["key"], True)


def _execute_selected_imports(importer):
    results = {
        "files_processed": 0,
        "new_items_added": 0,
        "items_updated":   0,
        "errors":          [],
        "source_files":    [],
    }
    doc_date = datetime.now().strftime("%Y-%m-%d")

    for d in st.session_state.import_data:
        analysis = d["analysis"]
        if not analysis:
            continue
        filename = d["filename"]

        selected_new = [
            i for i in analysis["new_items"]
            if _get_sel(filename, i["key"])
        ]
        selected_upd = [
            i for i in analysis["updates"]
            if _get_sel(filename, i["key"])
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
        results["source_files"].append({
            "filename": filename,
            "size":     d["size"],
            "new":      r.get("new_items_added", 0),
            "updated":  r.get("items_updated",   0),
        })

        if od.get_access_token():
            od.archive_file(filename, d["content"])

    st.session_state.import_results   = results
    st.session_state.import_committed = True
    st.rerun()

# ── end of import — file analysis + execution ─────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  IMPORT — REVIEW UI  (select / deselect items before committing)
# ──────────────────────────────────────────────────────────────────────────────

def _render_select_all_toggle(state, button_key: str, label_suffix: str = "") -> bool:
    icon  = _toggle_icon(state)
    label = f"{icon}  Select All{' ' + label_suffix if label_suffix else ''}"
    return st.button(label, key=button_key)


def _render_import_review(importer):
    data        = st.session_state.import_data
    total_files = len(data)
    total_new   = sum(len(d["analysis"]["new_items"]) for d in data if d["analysis"])
    total_upd   = sum(len(d["analysis"]["updates"])   for d in data if d["analysis"])
    total_skip  = sum(
        len(d["analysis"]["skipped"]) + len(d["analysis"]["errors"])
        for d in data if d["analysis"]
    )
    total_warn  = sum(len(d["parse_warnings"]) for d in data)

    # Placeholder — filled in AFTER all checkboxes write their values back to sel
    metrics_ph = st.empty()

    if total_warn:
        st.warning(f"⚠️ {total_warn} parsing issue(s) detected — see per-file details below.")

    st.markdown("---")

    # ── Global Select All + top Commit ──────────────────────────────
    # Note: global_state and counts computed fresh each render from sel dict
    gh1, gh2 = st.columns([3, 3])
    with gh1:
        if _render_select_all_toggle(
            _global_selection_state(), "glob_sel_top",
            f"({total_new + total_upd} items across {total_files} files)"
        ):
            _set_all_items(_global_selection_state() is not True)
            st.rerun()
    with gh2:
        sel_count_top = _count_selected()
        commit_label  = f"✅ Commit {sel_count_top} change{'s' if sel_count_top != 1 else ''}"
        if st.button(commit_label, key="commit_top", type="primary",
                     disabled=(sel_count_top == 0)):
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
        file_items = _all_items_for_file(d)
        file_new   = len(analysis["new_items"]) if analysis else 0
        file_upd   = len(analysis["updates"])   if analysis else 0

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
        safe_key   = re.sub(r'[^a-zA-Z0-9]', '_', filename)
        file_state = _file_selection_state(d)
        file_sel   = sum(1 for i in file_items if _get_sel(filename, i["key"]))

        fs1, _ = st.columns([3, 5])
        with fs1:
            if _render_select_all_toggle(
                file_state, f"fsel_{safe_key}",
                f"({file_sel}/{len(file_items)})"
            ):
                _set_file_items(d, file_state is not True)
                st.rerun()

        # Individual checkboxes — NO key=.
        # We own all state in sel dict. Checkbox return value is written
        # back to sel every render; metrics placeholder is filled after
        # all writes so counts are always current.
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

                label       = f"{tag} **{item['description']}** `{item['key'].split('||')[1]}`{change_note}"
                current_val = _get_sel(filename, item["key"])
                new_val     = st.checkbox(label, value=current_val)
                # Always write back — this is the only place sel is updated
                # for individual items (buttons do bulk writes + rerun)
                _set_sel(filename, item["key"], new_val)

        st.markdown("---")

    # ── Fill metrics placeholder now that all checkbox writes are done ──
    selected_final = _count_selected()
    with metrics_ph.container():
        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("Files",        total_files)
        mc2.metric("🆕 New Items", total_new)
        mc3.metric("🔄 Updates",   total_upd)
        mc4.metric("⏭️ Skipped",   total_skip)
        mc5.metric("✅ Selected",  selected_final)

    # ── Bottom commit bar ────────────────────────────────────────────
    bc1, bc2 = st.columns([3, 3])
    with bc1:
        if _render_select_all_toggle(
            _global_selection_state(), "glob_sel_bot",
            f"({total_new + total_upd} items across {total_files} files)"
        ):
            _set_all_items(_global_selection_state() is not True)
            st.rerun()
    with bc2:
        sel_count_bot  = _count_selected()
        commit_label2  = f"✅ Commit {sel_count_bot} change{'s' if sel_count_bot != 1 else ''}"
        if st.button(commit_label2, key="commit_bot", type="primary",
                     disabled=(sel_count_bot == 0)):
            _execute_selected_imports(importer)

# ── end of import — review UI ────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  IMPORT — POST-COMMIT RESULTS SCREEN
# ──────────────────────────────────────────────────────────────────────────────

def _render_import_results():
    r = st.session_state.import_results
    st.success("✅ Import committed successfully!")

    rc1, rc2, rc3 = st.columns(3)
    rc1.metric("Files Processed", r.get("files_processed", 0))
    rc2.metric("New Items Added",  r.get("new_items_added", 0))
    rc3.metric("Items Updated",    r.get("items_updated",   0))

    # Per-file breakdown
    source_files = r.get("source_files", [])
    if source_files:
        st.markdown("**Files included in this import:**")
        sf_cols = st.columns(3)
        for i, sf in enumerate(source_files):
            size_str = (
                f"{sf['size'] / 1024:.1f} KB"
                if sf["size"] < 1_048_576
                else f"{sf['size'] / 1_048_576:.1f} MB"
            )
            sf_cols[i % 3].markdown(
                f"📄 `{sf['filename']}`  \n"
                f"{size_str} &nbsp;·&nbsp; "
                f"🆕 {sf['new']} added &nbsp;·&nbsp; "
                f"🔄 {sf['updated']} updated"
            )

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

# ── end of import — post-commit results screen ───────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — IMPORT
# ──────────────────────────────────────────────────────────────────────────────

def page_import():
    st.title("📥 Import Files")
    _init_import_state()
    importer = get_importer()

    tab1, tab2 = st.tabs(["📤 Upload from Computer", "☁️ Import from OneDrive"])

    with tab1:
        # Show the uploader only when not yet committed
        if not st.session_state.import_committed:
            st.subheader("Upload Invoice or Inventory CSV / XLSX")
            uploaded = st.file_uploader(
                "Drop vendor invoice CSV or PAC export here",
                type=["csv", "xlsx"],
                accept_multiple_files=True,
                label_visibility="collapsed",
            )
        else:
            uploaded = None

        if uploaded:
            # Compact 3-column file grid — no pagination
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
                with status_bar.timed(f"Analyzing {len(uploaded)} invoice file(s)..."):
                    _analyze_uploaded_files(uploaded, importer)

            if st.session_state.import_data:
                _render_import_review(importer)

        elif st.session_state.import_committed:
            _render_import_results()

        else:
            # Files were cleared — reset state
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

# ── end of page — import ─────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — GL CODE MANAGER
# ──────────────────────────────────────────────────────────────────────────────

def page_gl_codes():
    st.title("🏷️ GL Code Manager")
    db = _get_db()
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

# ── end of page — GL code manager ────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — CHANGE HISTORY
# ──────────────────────────────────────────────────────────────────────────────

def page_history():
    st.title("📜 Change History")
    db = _get_db()

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

# ── end of page — change history ─────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — EXPORT
# ──────────────────────────────────────────────────────────────────────────────

def _sanitize_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare a DataFrame for openpyxl export:
    • Strip timezone from TIMESTAMPTZ columns (openpyxl rejects tz-aware datetimes)
    • Convert dict/list/JSONB columns to JSON strings
    • Convert any remaining non-serializable objects to str
    """
    df = df.copy()
    for col in df.columns:
        # Timezone-aware datetimes → naive UTC
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            try:
                df[col] = df[col].dt.tz_localize(None)
            except Exception:
                try:
                    df[col] = df[col].dt.tz_convert(None)
                except Exception:
                    df[col] = df[col].astype(str)
        else:
            # Check for object columns containing dicts, lists, or mixed types
            sample = df[col].dropna()
            if not sample.empty and isinstance(sample.iloc[0], (dict, list)):
                import json as _json
                df[col] = df[col].apply(
                    lambda v: _json.dumps(v) if isinstance(v, (dict, list)) else v
                )
    return df


def page_export():
    st.title("📤 Export")
    db = _get_db()

    st.subheader("Export Full Inventory")
    items = db.get_all_items()
    if items:
        df      = pd.DataFrame(items)
        df_safe = _sanitize_for_excel(df)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_safe.to_excel(writer, index=False, sheet_name="Inventory")
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
            df_safe.to_excel(buffer, index=False)
            filename = f"inventory_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            od.archive_file(filename, buffer.getvalue(), subfolder="Exports")
            st.success(f"Saved {filename} to OneDrive Archives.")

# ── end of page — export ─────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — COUNT IMPORT  (myOrders CSV / XLSX / PDF count exports)
# ──────────────────────────────────────────────────────────────────────────────

def _init_count_state():
    defaults = {
        "count_records":    None,   # List[CountRecord] after parse
        "count_variance":   None,   # List[VarianceRecord] after diff
        "count_fmt":        None,   # format_info dict
        "count_committed":  False,
        "count_results":    None,
        "count_file_name":  "",
        "count_file_bytes": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def page_count_overrides():
    """Override & Rule Manager — MOG fix rules and count correction overrides."""
    db = get_db()
    st.title("⚙️ Override & Rule Manager")
    st.caption(
        "Manage count correction rules for items whose pack ratios cannot be changed "
        "in the source system (e.g. MOG items). Rules are applied automatically during "
        "every count import."
    )

    # ── Load & persist user preference defaults ───────────────────────────────
    # These are the sticky defaults for the Add/Edit form — user changes are
    # saved back to DB so they persist across sessions.
    if "ovr_default_scope" not in st.session_state:
        st.session_state["ovr_default_scope"] = db.get_override_setting(
            "default_scope", "global"
        )
    if "ovr_default_case_behavior" not in st.session_state:
        st.session_state["ovr_default_case_behavior"] = db.get_override_setting(
            "default_case_behavior", "ignore"
        )

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_rules, tab_add, tab_prefs = st.tabs(
        ["📋 Active Rules", "➕ Add / Edit Rule", "🔧 Default Preferences"]
    )

    # ────────────────────────────────────────────────────────────────────────
    # TAB 1 — Active Rules table
    # ────────────────────────────────────────────────────────────────────────
    with tab_rules:
        rules = db.get_all_count_overrides()
        if not rules:
            st.info("No override rules defined yet. Use the **Add / Edit Rule** tab to create one.")
        else:
            st.caption(f"{len(rules)} active rule(s)")
            for rule in rules:
                scope_label = (
                    "🌐 Global" if rule["cost_center"] == "*"
                    else f"📍 {rule['cost_center']}"
                )
                case_label = (
                    "Ignore case qty" if rule["case_behavior"] == "ignore"
                    else "Add case qty (÷ divisor + case × conv)"
                )
                desc  = rule.get("description") or rule["item_key"]
                pt    = rule.get("pack_type") or "—"
                notes = rule.get("notes") or ""

                with st.expander(
                    f"**{desc}** `{rule['item_key']}`  |  ÷ {rule['divisor']}  |  {scope_label}",
                    expanded=False,
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Divisor",       rule["divisor"])
                    c2.metric("Scope",         scope_label)
                    c3.metric("Case behavior", case_label)
                    st.text(f"Pack type: {pt}")
                    if notes:
                        st.text(f"Notes: {notes}")
                    if st.button(
                        "🗑️ Delete rule",
                        key=f"del_{rule['item_key']}_{rule['cost_center']}",
                        type="secondary",
                    ):
                        db.delete_count_override(rule["item_key"], rule["cost_center"])
                        st.success(f"Rule deleted for {desc}")
                        st.rerun()

    # ────────────────────────────────────────────────────────────────────────
    # TAB 2 — Add / Edit Rule
    # ────────────────────────────────────────────────────────────────────────
    with tab_add:
        st.markdown("#### Add or update a count correction rule")
        st.caption(
            "Tip: for MOG items with inverted pack ratios (e.g. 1LB Tray — the system "
            "counts individual trays but expects cases of 1000), set **divisor** to 1000 "
            "and **case behavior** to *Ignore case qty*."
        )

        # Pre-populate scope/behavior from saved defaults
        default_scope    = st.session_state["ovr_default_scope"]
        default_behavior = st.session_state["ovr_default_case_behavior"]

        with st.form("add_override_form", clear_on_submit=True):
            st.markdown("**Item key**")
            st.caption("Format: `ITEM NAME||PACK_TYPE` (uppercase, double-pipe). "
                       "Find it on the Items page.")
            item_key = st.text_input(
                "Item key", placeholder="1LB TRAY||1/1000", label_visibility="collapsed"
            )

            divisor = st.number_input(
                "Divisor (each qty ÷ divisor = corrected each qty)",
                min_value=0.0001, value=1000.0, step=1.0, format="%.4f"
            )

            scope_opts = ["global", "per cost center"]
            scope = st.selectbox(
                "Scope",
                scope_opts,
                index=scope_opts.index(default_scope) if default_scope in scope_opts else 0,
                help="Global = applies to this item at every location. "
                     "Per cost center = only applies at the specified cost center.",
            )

            cost_center = "*"
            if scope == "per cost center":
                cost_center = st.text_input(
                    "Cost center name",
                    placeholder="e.g. Ferttita",
                    help="Must match the cost center prefix from the export file.",
                )

            behavior_opts  = ["ignore", "add"]
            behavior_labels = [
                "Ignore case qty — only use each ÷ divisor",
                "Add case qty — (each ÷ divisor) + (case × conv)",
            ]
            behavior_idx = behavior_opts.index(default_behavior) if default_behavior in behavior_opts else 0
            behavior = st.selectbox(
                "Case quantity behavior",
                behavior_opts,
                index=behavior_idx,
                format_func=lambda x: behavior_labels[behavior_opts.index(x)],
            )

            notes = st.text_input("Notes (optional)", placeholder="e.g. MOG item — tray count inverted")

            save_as_default = st.checkbox(
                "Save scope & case behavior as my new defaults",
                value=False,
            )

            submitted = st.form_submit_button("💾 Save Rule", type="primary")

        if submitted:
            if not item_key.strip():
                st.error("Item key is required.")
            else:
                cc = cost_center.strip() if scope == "per cost center" else "*"
                ok = db.upsert_count_override(
                    item_key     = item_key.strip().upper(),
                    divisor      = float(divisor),
                    case_behavior= behavior,
                    cost_center  = cc,
                    notes        = notes.strip(),
                    created_by   = "user",
                )
                if ok:
                    st.success(f"✅ Rule saved for `{item_key.strip().upper()}`")
                    if save_as_default:
                        db.set_override_setting("default_scope",         scope)
                        db.set_override_setting("default_case_behavior", behavior)
                        st.session_state["ovr_default_scope"]         = scope
                        st.session_state["ovr_default_case_behavior"] = behavior
                        st.info("Default preferences updated.")
                else:
                    st.error("Failed to save rule. Check the item key and try again.")

    # ────────────────────────────────────────────────────────────────────────
    # TAB 3 — Default Preferences
    # ────────────────────────────────────────────────────────────────────────
    with tab_prefs:
        st.markdown("#### Default preferences for new rules")
        st.caption(
            "These defaults pre-fill the Add / Edit Rule form. They can also be "
            "updated inline when saving any rule."
        )

        scope_opts     = ["global", "per cost center"]
        behavior_opts  = ["ignore", "add"]
        behavior_labels = [
            "Ignore case qty — only use each ÷ divisor",
            "Add case qty — (each ÷ divisor) + (case × conv)",
        ]

        pref_scope = st.selectbox(
            "Default scope",
            scope_opts,
            index=scope_opts.index(st.session_state["ovr_default_scope"])
                  if st.session_state["ovr_default_scope"] in scope_opts else 0,
            key="pref_scope_select",
        )
        pref_behavior = st.selectbox(
            "Default case behavior",
            behavior_opts,
            index=behavior_opts.index(st.session_state["ovr_default_case_behavior"])
                  if st.session_state["ovr_default_case_behavior"] in behavior_opts else 0,
            format_func=lambda x: behavior_labels[behavior_opts.index(x)],
            key="pref_behavior_select",
        )

        if st.button("💾 Save Preferences", type="primary"):
            db.set_override_setting("default_scope",         pref_scope)
            db.set_override_setting("default_case_behavior", pref_behavior)
            st.session_state["ovr_default_scope"]         = pref_scope
            st.session_state["ovr_default_case_behavior"] = pref_behavior
            st.success("Preferences saved.")


def page_count_import():
    from count_importer import CountImportMeta

    st.title("📋 Count Import")
    st.caption("Import on-hand counts from myOrders CSV, XLSX, or PDF exports.")
    _init_count_state()
    ci = get_count_importer()

    # ── RESULTS SCREEN (after commit) ────────────────────────────────────────
    if st.session_state.count_committed and st.session_state.count_results:
        _render_count_results()
        return

    # ── STEP 1 — UPLOAD ──────────────────────────────────────────────────────
    st.subheader("Step 1 — Upload Count File")
    uploaded = st.file_uploader(
        "Drop myOrders count export here (CSV, XLSX, or PDF)",
        type=["csv", "xlsx", "pdf"],
        label_visibility="collapsed",
    )

    if not uploaded:
        # Show prior import history
        _render_count_history()
        return

    # Re-parse only when a new file is uploaded
    if uploaded.name != st.session_state.count_file_name:
        content = uploaded.read()
        st.session_state.count_file_bytes = content
        st.session_state.count_file_name  = uploaded.name
        st.session_state.count_records    = None
        st.session_state.count_variance   = None
        st.session_state.count_committed  = False
        st.session_state.count_results    = None

        with status_bar.timed(f"Parsing {uploaded.name}..."):
            t0 = time.perf_counter()
            records, fmt = ci.parse(uploaded.name, content)
            parse_elapsed = time.perf_counter() - t0
        st.session_state.count_records = records
        st.session_state.count_fmt     = fmt
        st.session_state["parse_elapsed"] = parse_elapsed

        if ci.errors:
            for e in ci.errors:
                st.error(e)
            return

    records = st.session_state.count_records
    fmt     = st.session_state.count_fmt or {}

    if not records:
        st.warning("No records found in this file. Check that it is a valid count export.")
        return

    # ── STEP 2 — FILE SUMMARY + OPTIONS ──────────────────────────────────────
    st.markdown("---")
    st.subheader("Step 2 — Confirm Details")

    fi1, fi2, fi3, fi4 = st.columns(4)
    fi1.metric("Format",    fmt.get("ext", "?").upper())
    fi2.metric("Layout",    fmt.get("layout", "?").capitalize())
    fi3.metric("Items",     fmt.get("record_count", len(records)))
    fi4.metric("Locations", len(fmt.get("locations", [])))

    st.caption(
        f"📄 {uploaded.name}  ·  Detected: {fmt.get('description', '')}"
        + (f"  ·  ⏱ parsed in {st.session_state.get('parse_elapsed', 0):.2f}s"
           if st.session_state.get('parse_elapsed') else "")
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        count_date = st.date_input(
            "Count Date",
            value=datetime.now().date(),
        )
    with col2:
        count_type = st.radio(
            "Count Type",
            ["complete", "partial"],
            horizontal=True,
            help="Complete: overwrites all qty. Partial: updates listed items only.",
        )
    with col3:
        location_filter = st.multiselect(
            "Limit to locations (optional)",
            options=fmt.get("locations", []),
            placeholder="All locations",
        )
    with col1:
        flag_each  = st.number_input("Flag threshold — units", value=24, min_value=1)
    with col2:
        flag_value = st.number_input("Flag threshold — value ($)", value=50.0, min_value=0.0, format="%.2f")

    # Filter records if location scope selected
    work_records = records
    if location_filter:
        work_records = [r for r in records if r.location in location_filter]

    # ── STEP 3 — VARIANCE PREVIEW ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Step 3 — Variance Preview")

    # Recalculate when options change
    calc_key = f"{uploaded.name}|{flag_each}|{flag_value}|{'|'.join(sorted(location_filter))}"
    if (st.session_state.count_variance is None or
            st.session_state.get("_calc_key") != calc_key):
        with status_bar.timed(f"Variance diff — {len(work_records)} records..."):
            t0 = time.perf_counter()
            variance = ci.calculate_variance(
                work_records,
                flag_each_threshold  = int(flag_each),
                flag_value_threshold = float(flag_value),
            )
            var_elapsed = time.perf_counter() - t0
        st.session_state.count_variance      = variance
        st.session_state["_calc_key"]        = calc_key
        st.session_state["variance_elapsed"] = var_elapsed
    else:
        variance = st.session_state.count_variance

    flagged    = [v for v in variance if v.is_flagged]
    not_in_db  = [v for v in variance if not v.in_db]
    net_value  = sum(v.variance_value for v in variance)

    vc1, vc2, vc3, vc4 = st.columns(4)
    vc1.metric("Total Items",     len(variance))
    vc2.metric("🚩 Flagged",      len(flagged),   delta=None)
    vc3.metric("❓ Not in DB",    len(not_in_db))
    vc4.metric("Net Value Δ",     f"${net_value:+,.2f}")

    timing_parts = []
    if st.session_state.get("parse_elapsed"):
        timing_parts.append(f"⏱ parse {st.session_state['parse_elapsed']:.2f}s")
    if st.session_state.get("variance_elapsed"):
        timing_parts.append(f"variance diff {st.session_state['variance_elapsed']:.2f}s")
    if timing_parts:
        st.caption("  ·  ".join(timing_parts))

    tab_flag, tab_all = st.tabs([
        f"🚩 Flagged ({len(flagged)})",
        f"📋 All Items ({len(variance)})",
    ])

    def _variance_df(rows):
        return pd.DataFrame([{
            "Location":      v.record.location,
            "Description":   v.record.item_description,
            "Pack Type":     v.record.pack_type,
            "Prev Qty":      round(v.db_qty, 2),
            "New Qty":       round(v.new_qty, 2),
            "Variance":      f"{v.variance_each:+.2f}",
            "Δ Value":       f"${v.variance_value:+,.2f}",
            "In DB":         "✅" if v.in_db else "❌",
            "Flag":          "🚩 " + v.flag_reason if v.is_flagged else "",
        } for v in rows])

    with tab_flag:
        if flagged:
            st.dataframe(_variance_df(flagged), use_container_width=True, hide_index=True)
        else:
            st.success("No items exceed variance thresholds.")

    # ──────────────────────────────────────────────────────────────────────────────
#  PATCH — Diagnostic CSV Export for Count Import Variance
#
#  In app.py, find this line (around line 1135):
#
#      with tab_all:
#          loc_opts = ["All"] + sorted(set(v.record.location for v in variance))
#          sel_loc  = st.selectbox("Filter by location", loc_opts, key="var_loc_filter")
#          show_rows = variance if sel_loc == "All" else [
#              v for v in variance if v.record.location == sel_loc
#          ]
#          st.dataframe(_variance_df(show_rows), use_container_width=True, hide_index=True)
#
#  REPLACE that entire `with tab_all:` block with the version below.
#  The export button also appears outside the tabs so it's always visible.
# ──────────────────────────────────────────────────────────────────────────────

# ── REPLACE the existing `with tab_all:` block with this ─────────────────────

    with tab_all:
        loc_opts  = ["All"] + sorted(set(v.record.location for v in variance))
        sel_loc   = st.selectbox("Filter by location", loc_opts, key="var_loc_filter")
        show_rows = variance if sel_loc == "All" else [
            v for v in variance if v.record.location == sel_loc
        ]
        st.dataframe(_variance_df(show_rows), use_container_width=True, hide_index=True)

    # ── Diagnostic export — always visible, outside the tabs ─────────────────
    #
    #  Includes every field needed to trace the $33K vs $102K discrepancy:
    #    count_qty_case / count_qty_each — raw counted quantities
    #    price_case / price_each         — per-unit prices from export
    #    total_price                     — authoritative pre-computed value
    #    computed_value                  — what new_qty × price_each would give
    #    variance_value                  — what was actually recorded
    #    delta                           — total_price minus computed_value
    #
    st.markdown("---")
    col_exp, col_sum = st.columns([2, 2])

    with col_sum:
        sum_total_price    = sum(v.record.total_price   for v in variance)
        sum_computed_value = sum(
            v.new_qty * (v.record.price_each if v.record.price_each > 0
                         else v.db_price_each)
            for v in variance
        )
        sum_variance_value = sum(v.variance_value for v in variance)
        st.caption("**Totals — pre-export diagnostic**")
        d1, d2, d3 = st.columns(3)
        d1.metric("SUM(total_price)",    f"${sum_total_price:,.2f}",
                  help="Direct sum of pre-computed Total Price from export rows.")
        d2.metric("SUM(qty × price)",    f"${sum_computed_value:,.2f}",
                  help="Recomputed: new_qty × price_each. Should differ from above if conv_ratio is missing.")
        d3.metric("Delta",               f"${sum_total_price - sum_computed_value:+,.2f}",
                  help="Positive = total_price is higher. This is the gap being diagnosed.")

    with col_exp:
        def _diagnostic_df(rows):
            out = []
            for v in rows:
                rec           = v.record
                eff_price     = rec.price_each if rec.price_each > 0 else v.db_price_each
                computed_val  = round(v.new_qty * eff_price, 4)
                out.append({
                    "location":         rec.location,
                    "seq":              rec.seq,
                    "description":      rec.item_description,
                    "pack_type":        rec.pack_type,
                    "item_key":         rec.item_key,
                    # --- counts ---
                    "count_qty_case":   rec.count_qty_case,
                    "count_qty_each":   rec.count_qty_each,
                    "new_qty":          round(v.new_qty, 4),
                    # --- prices ---
                    "price_case":       rec.price_case,
                    "price_each":       rec.price_each,
                    "db_price_each":    round(v.db_price_each, 4),
                    # --- values ---
                    "total_price":      rec.total_price,      # from export (authoritative)
                    "computed_value":   computed_val,         # new_qty × price_each
                    "delta":            round(rec.total_price - computed_val, 4),
                    "variance_value":   round(v.variance_value, 2),
                    # --- db state ---
                    "db_qty":           round(v.db_qty, 4),
                    "in_db":            v.in_db,
                    "is_flagged":       v.is_flagged,
                    "flag_reason":      v.flag_reason,
                    "is_chargeable":    rec.is_chargeable,
                    # --- uom ---
                    "uom_case":         rec.uom_case,
                    "uom_each":         rec.uom_each,
                })
            return pd.DataFrame(out)

        diag_df = _diagnostic_df(variance)

        st.download_button(
            label       = "⬇️ Export Diagnostic CSV",
            data        = diag_df.to_csv(index=False).encode(),
            file_name   = (
                f"variance_diag_{uploaded.name.rsplit('.',1)[0]}"
                f"_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            ),
            mime        = "text/csv",
            use_container_width=True,
            help=(
                "Full diagnostic export: raw counts, prices, total_price from export, "
                "computed value, and delta. Use to trace the $33K vs $102K discrepancy."
            ),
        )

# ── end of patch ──────────────────────────────────────────────────────────────

    # ── STEP 4 — COMMIT ───────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Step 4 — Commit Count")

    items_in_db   = [v for v in variance if v.in_db]
    items_missing = [v for v in variance if not v.in_db]

    if items_missing:
        st.info(
            f"ℹ️ **{len(items_missing)} item(s) not currently in the database.** "
            f"Use the option below to add them automatically from the count data, "
            f"or skip them and add manually later."
        )
        add_missing = st.toggle(
            f"➕ Add {len(items_missing)} unmatched item(s) to the database from this count",
            value=True,
            help=(
                "Creates a new DB record for each unmatched item using the description, "
                "pack type, and price from the count file. GL code, vendor, and other "
                "details can be filled in later via the Inventory page or a vendor import."
            ),
        )
    else:
        add_missing = False

    items_to_write = len(variance) if add_missing else len(items_in_db)
    items_new_note = f" ({len(items_missing)} new + {len(items_in_db)} updates)" if add_missing and items_missing else ""

    confirm = st.checkbox(
        f"I confirm: commit {items_to_write} item(s){items_new_note} "
        f"from {count_type} count dated {count_date}",
        key="count_confirm",
    )

    commit_btn = st.button(
        f"✅ Commit Count — {items_to_write} items{items_new_note}",
        type="primary",
        disabled=not confirm or items_to_write == 0,
    )

    if commit_btn:
        meta = CountImportMeta(
            source_file  = uploaded.name,
            file_format  = fmt.get("ext", ""),
            data_layout  = fmt.get("layout", ""),
            count_type   = count_type,
            count_date   = str(count_date),
            imported_by  = "user",
        )
        with status_bar.timed(f"Committing {items_to_write} items to database..."):
            results = ci.execute_count_import(variance, meta, add_missing=add_missing)
        st.session_state.count_results   = results
        st.session_state.count_committed = True
        st.rerun()


def _render_count_results():
    r = st.session_state.count_results
    if not r:
        return

    st.success("✅ Count import committed successfully!")
    rc1, rc2, rc3, rc4, rc5 = st.columns(5)
    rc1.metric("Import ID",      r.get("import_id", ""))
    rc2.metric("➕ Items Created", r.get("items_created", 0))
    rc3.metric("🔄 Items Updated", r.get("items_updated", 0))
    rc4.metric("⏭️ Items Skipped", r.get("items_skipped", 0))
    rc5.metric("🚩 Flagged",      r.get("items_flagged", 0))

    net = r.get("total_new_value", 0) - r.get("total_prev_value", 0)
    vc1, vc2, vc3 = st.columns(3)
    vc1.metric("Prev Inventory Value", f"${r.get('total_prev_value', 0):,.2f}")
    vc2.metric("New Inventory Value",  f"${r.get('total_new_value', 0):,.2f}")
    vc3.metric("Net Δ Value",          f"${net:+,.2f}")

    if r.get("errors"):
        with st.expander(f"⚠️ {len(r['errors'])} error(s)"):
            for e in r["errors"]:
                st.caption(e)

    # Variance detail from DB
    db = _get_db()
    detail = db.get_count_variance_detail(r["import_id"])
    if detail:
        st.markdown("---")
        st.subheader("Variance Detail")
        flagged_only = st.checkbox("Show flagged only", value=True)
        rows = [d for d in detail if d["is_flagged"]] if flagged_only else detail
        if rows:
            st.dataframe(pd.DataFrame(rows)[[
                "location", "item_description", "pack_type",
                "prev_qty_each", "new_qty_each", "variance_each",
                "variance_value", "is_flagged", "flag_reason",
            ]], use_container_width=True, hide_index=True)

    st.markdown("---")
    if st.button("📋 Import Another Count"):
        for k in ("count_records", "count_variance", "count_fmt",
                  "count_committed", "count_results",
                  "count_file_name", "count_file_bytes", "_calc_key"):
            st.session_state[k] = None if k not in (
                "count_committed",) else False
        st.rerun()


def _render_count_history():
    db = _get_db()
    try:
        imports = db.get_count_imports(limit=10)
    except Exception as e:
        st.info(
            "Count import history is not available yet — the count tables may still be "
            "initializing. Try rebooting the app from the Streamlit Cloud dashboard, "
            "or upload a file above to trigger table creation."
        )
        st.caption(f"Detail: {e}")
        return
    if not imports:
        st.info("No count imports on record yet. Upload a count file above to get started.")
        return

    st.markdown("---")
    st.subheader("Recent Count Imports")
    df = pd.DataFrame(imports)[[
        "count_date", "source_file", "cost_center", "count_type",
        "total_items", "items_changed", "items_flagged",
        "total_prev_value", "total_new_value", "variance_value", "imported_by",
    ]]
    df["total_prev_value"] = df["total_prev_value"].apply(lambda x: f"${float(x or 0):,.2f}")
    df["total_new_value"]  = df["total_new_value"].apply(lambda x: f"${float(x or 0):,.2f}")
    df["variance_value"]   = df["variance_value"].apply(lambda x: f"${float(x or 0):+,.2f}")
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Drill-down into a specific import
    import_ids = [i["import_id"] for i in imports]
    sel_id = st.selectbox(
        "View variance detail for import",
        ["— select —"] + import_ids,
        key="count_hist_sel",
    )
    if sel_id and sel_id != "— select —":
        detail      = db.get_count_variance_detail(sel_id)
        flagged_det = [d for d in detail if d["is_flagged"]]
        st.caption(f"{len(detail)} items · {len(flagged_det)} flagged")
        if detail:
            flagged_only = st.checkbox("Flagged only", value=bool(flagged_det), key="hist_flag_chk")
            rows = flagged_det if flagged_only else detail
            st.dataframe(pd.DataFrame(rows)[[
                "location", "item_description", "pack_type",
                "prev_qty_each", "new_qty_each", "variance_each",
                "variance_value", "is_flagged", "flag_reason",
            ]], use_container_width=True, hide_index=True)

# ── end of page — count import ────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — SETTINGS
# ──────────────────────────────────────────────────────────────────────────────

def page_settings():
    st.title("⚙️ Settings")
    reg = st.session_state["_registry"]

    tab_feat, tab_sidebar, tab_prefs = st.tabs([
        "🔧 Feature Toggles",
        "◀️ Sidebar",
        "👤 Preferences",
    ])

    with tab_feat:
        st.subheader("Feature Toggles")
        st.caption("Enabled features are live. Disabled features appear greyed out in the menu.")
        for ft in reg.all_features():
            col1, col2 = st.columns([3, 1])
            col1.write(f"**{ft.name}** — {ft.description}")
            new_val = col2.toggle(
                "Enabled", value=ft.option_available,
                key=f"ft_{ft.name}",
            )
            if new_val != ft.option_available:
                reg.set(ft.name, new_val)
                st.rerun()

    with tab_sidebar:
        st.subheader("Sidebar Settings")
        cfg = st.session_state["_sidebar_cfg"]
        cfg.visible        = st.toggle("Show Sidebar",           value=cfg.visible)
        cfg.show_nav       = st.toggle("Show nav links",         value=cfg.show_nav)
        cfg.show_cost_center = st.toggle("Show cost center badge", value=cfg.show_cost_center)
        cfg.show_recent    = st.toggle("Show recent imports",    value=cfg.show_recent)
        cfg.custom_label   = st.text_input("Sidebar label",      value=cfg.custom_label)
        st.session_state["_sidebar_cfg"] = cfg
        if st.button("Apply"):
            st.rerun()

    with tab_prefs:
        st.subheader("Preferences")
        st.info("User preferences coming soon.")

# ── end of page — settings ────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE — DATABASE MANAGEMENT
#  Paste this function into app.py, then add the two lines noted at the
#  bottom to wire it into main().
#
#  Operations covered:
#    Backup · Restore · Duplicate · Create New · Rename / Relabel
#    Assign Cost Center(s) · Set Variance Thresholds
#    Clear Quantities · Clear All Items · Full Reset
#
#  Safety rules:
#    • All destructive operations require a two-step confirm
#    • Full Reset requires typing the cost center name to unlock
#    • Clear operations show a row-count preview before confirming
#    • Restore shows backup metadata before confirming
# ──────────────────────────────────────────────────────────────────────────────

def page_db_management():
    from session_state import init_session_state, reset_db_mgmt_confirm
    from ui_skeleton   import DB_OPERATIONS

    init_session_state(st.session_state)
    db = _get_db()

    st.title("🗄️ Database Management")
    st.caption("Backup, restore, configure, and maintain your inventory database.")

    # ── Confirm-state helper ─────────────────────────────────────────────────
    def _mgmt(key, default=None):
        return st.session_state["db_mgmt"].get(key, default)

    def _set_mgmt(key, val):
        st.session_state["db_mgmt"][key] = val

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab_backup, tab_structure, tab_thresholds, tab_danger = st.tabs([
        "💾  Backup & Restore",
        "🏗️  Structure",
        "🎚️  Thresholds",
        "⚠️  Danger Zone",
    ])

    # ── end of tabs setup ─────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  TAB — BACKUP & RESTORE
    # ──────────────────────────────────────────────────────────────────────────

    with tab_backup:
        st.subheader("💾 Backup")

        col_info, col_action = st.columns([2, 1])

        with col_info:
            last_ts    = _mgmt("last_backup_ts")
            last_label = _mgmt("last_backup_label")
            if last_ts:
                st.success(f"Last backup: **{last_label or 'unlabeled'}** — {last_ts}")
            else:
                st.info("No backup on record for this session.")

        with col_action:
            backup_label = st.text_input(
                "Backup label (optional)",
                placeholder="e.g. pre-import 2026-03-12",
                key="backup_label_input",
            )
            if st.button("💾 Create Backup Now", use_container_width=True):
                try:
                    ts    = datetime.now().strftime("%Y-%m-%d %H:%M")
                    label = backup_label.strip() or f"backup {ts}"
                    # Export full items table to JSON stored in Streamlit state
                    items = db.get_all_items(None)
                    import json
                    snapshot = {
                        "label":     label,
                        "ts":        ts,
                        "item_count": len(items),
                        "items":     items,
                    }
                    if "db_backups" not in st.session_state:
                        st.session_state["db_backups"] = []
                    st.session_state["db_backups"].append(snapshot)
                    _set_mgmt("last_backup_ts",    ts)
                    _set_mgmt("last_backup_label", label)
                    st.success(f"✅ Backup created: **{label}** ({len(items)} items)")
                    st.rerun()
                except Exception as e:
                    st.error(f"Backup failed: {e}")

        st.markdown("---")
        st.subheader("⬇️ Download Backup")

        items_all = db.get_all_items(None)
        if items_all:
            import json, io as _io
            dl_label = backup_label.strip() if 'backup_label_input' in st.session_state else "backup"
            dl_bytes = json.dumps(items_all, indent=2, default=str).encode()
            st.download_button(
                label    = "⬇️ Download as JSON",
                data     = dl_bytes,
                file_name= f"uha_inventory_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                mime     = "application/json",
                use_container_width=True,
            )
            df_export = pd.DataFrame(items_all)
            csv_bytes = df_export.to_csv(index=False).encode()
            st.download_button(
                label    = "⬇️ Download as CSV",
                data     = csv_bytes,
                file_name= f"uha_inventory_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime     = "text/csv",
                use_container_width=True,
            )
        else:
            st.info("No items in database to export.")

        st.markdown("---")
        st.subheader("⏪ Restore from Session Backup")

        backups = st.session_state.get("db_backups", [])
        if not backups:
            st.info("No session backups available. Create a backup above first.")
        else:
            options = {f"{b['label']} ({b['item_count']} items — {b['ts']})": i
                       for i, b in enumerate(backups)}
            chosen_label = st.selectbox(
                "Select backup to restore",
                options=list(options.keys()),
                key="restore_select",
            )
            chosen_idx = options[chosen_label]
            chosen     = backups[chosen_idx]

            st.warning(
                f"⚠️ Restoring **{chosen['label']}** will overwrite all "
                f"{chosen['item_count']} items currently in the database."
            )

            confirmed = _mgmt("restore_confirmed", False)
            if not confirmed:
                if st.button("🔓 Unlock Restore", type="secondary"):
                    _set_mgmt("restore_confirmed", True)
                    st.rerun()
            else:
                col_go, col_cancel = st.columns(2)
                with col_go:
                    if st.button("⏪ Confirm Restore", type="primary",
                                 use_container_width=True):
                        try:
                            # Restore: clear items table, re-insert from snapshot
                            with db.get_conn() as conn:  # type: ignore
                                cur = conn.cursor()
                                cur.execute("DELETE FROM items")
                            for item in chosen["items"]:
                                try:
                                    db.add_item(item, changed_by="restore")
                                except Exception:
                                    db.upsert_item(item, changed_by="restore")
                            reset_db_mgmt_confirm(st.session_state)
                            st.success(
                                f"✅ Restored {len(chosen['items'])} items "
                                f"from **{chosen['label']}**"
                            )
                            st.rerun()
                        except Exception as e:
                            st.error(f"Restore failed: {e}")
                with col_cancel:
                    if st.button("Cancel", use_container_width=True):
                        reset_db_mgmt_confirm(st.session_state)
                        st.rerun()

    # ── end of tab — backup & restore ─────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  TAB — STRUCTURE  (rename, cost center, duplicate, create new)
    # ──────────────────────────────────────────────────────────────────────────

    with tab_structure:

        # ── Current database info ────────────────────────────────────────────
        st.subheader("📊 Current Database")
        try:
            total_items   = db.count_items(None)
            active_items  = db.count_items("active")
            total_value   = db.get_inventory_value()
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Records",  f"{total_items:,}")
            col2.metric("Active Items",   f"{active_items:,}")
            col3.metric("Inventory Value",f"${total_value:,.2f}")
        except Exception as e:
            st.error(f"Could not read DB stats: {e}")

        st.markdown("---")

        # ── Rename / Relabel ─────────────────────────────────────────────────
        st.subheader("✏️ Rename / Relabel")
        sidebar_cfg = st.session_state.get("_sidebar_cfg")
        current_label = sidebar_cfg.custom_label if sidebar_cfg else "UHA TDECU Stadium"

        new_label = st.text_input(
            "Display name (shown in sidebar + reports)",
            value=current_label,
            key="db_rename_input",
        )
        if st.button("Apply Label", key="db_rename_apply"):
            if sidebar_cfg and new_label.strip():
                sidebar_cfg.custom_label = new_label.strip()
                st.session_state["_sidebar_cfg"] = sidebar_cfg
                st.success(f"✅ Label updated to: **{new_label.strip()}**")
                st.rerun()

        st.markdown("---")

        # ── Cost Center Assignment ───────────────────────────────────────────
        st.subheader("🏷️ Cost Center Assignment")
        st.caption(
            "Assign this database to one or more cost center keys. "
            "The active cost center controls which DB connection is used for imports."
        )

        current_cc = st.session_state["db"].get("cost_center", "default")
        st.info(f"Active cost center: **{current_cc}**")

        new_cc_name = st.text_input(
            "New cost center name",
            value=_mgmt("pending_cc_name", ""),
            placeholder="e.g. TDECU Stadium",
            key="cc_name_input",
        )
        new_cc_key = re.sub(r'[^a-z0-9_]', '_',
                            new_cc_name.strip().lower()).strip('_')
        if new_cc_name.strip():
            st.caption(f"Key will be: `{new_cc_key}`")

        new_cc_desc = st.text_input(
            "Description (optional)",
            value=_mgmt("pending_cc_desc", ""),
            placeholder="e.g. UHA TDECU Stadium — Compass 57231",
            key="cc_desc_input",
        )

        col_add, col_switch = st.columns(2)
        with col_add:
            if st.button("➕ Register Cost Center", use_container_width=True):
                if new_cc_key:
                    available = st.session_state["db"].get("available", [])
                    if not any(cc["key"] == new_cc_key for cc in available):
                        available.append({
                            "key":         new_cc_key,
                            "name":        new_cc_name.strip(),
                            "description": new_cc_desc.strip(),
                        })
                        st.session_state["db"]["available"] = available
                    st.success(f"✅ Cost center registered: **{new_cc_key}**")
                    st.rerun()

        with col_switch:
            available = st.session_state["db"].get("available", [])
            cc_options = [cc["key"] for cc in available] or [current_cc]
            selected_cc = st.selectbox(
                "Switch active cost center",
                options=cc_options,
                index=cc_options.index(current_cc) if current_cc in cc_options else 0,
                key="cc_switch_select",
            )
            if st.button("🔀 Switch", use_container_width=True):
                st.session_state["db"]["cost_center"] = selected_cc
                st.success(f"✅ Active cost center → **{selected_cc}**")
                st.rerun()

        st.markdown("---")

        # ── Duplicate / Create New ───────────────────────────────────────────
        st.subheader("📋 Duplicate or Create New Database")
        col_dup, col_new = st.columns(2)

        with col_dup:
            st.markdown("**Duplicate current database**")
            st.caption("Clone all items to a new cost center.")
            dup_target = st.text_input(
                "Target cost center key",
                placeholder="e.g. tdecu_copy",
                key="dup_target_input",
            )
            if st.button("📋 Duplicate", use_container_width=True, key="dup_btn"):
                if dup_target.strip():
                    try:
                        items = db.get_all_items(None)
                        duped = 0
                        for item in items:
                            item_copy = dict(item)
                            item_copy["cost_center"] = dup_target.strip()
                            # Key stays the same — cost_center is metadata only
                            try:
                                db.add_item(item_copy, changed_by="duplicate")
                                duped += 1
                            except Exception:
                                pass
                        st.success(f"✅ Duplicated {duped} items → **{dup_target.strip()}**")
                    except Exception as e:
                        st.error(f"Duplicate failed: {e}")
                else:
                    st.warning("Enter a target cost center key first.")

        with col_new:
            st.markdown("**Create new empty database**")
            st.caption("Register a new cost center with no items.")
            new_db_key = st.text_input(
                "New cost center key",
                placeholder="e.g. softball_complex",
                key="new_db_key_input",
            )
            if st.button("➕ Create Empty", use_container_width=True, key="new_db_btn"):
                if new_db_key.strip():
                    available = st.session_state["db"].get("available", [])
                    key_clean = re.sub(r'[^a-z0-9_]', '_',
                                       new_db_key.strip().lower()).strip('_')
                    if not any(cc["key"] == key_clean for cc in available):
                        available.append({
                            "key":         key_clean,
                            "name":        new_db_key.strip(),
                            "description": "New cost center",
                        })
                        st.session_state["db"]["available"] = available
                        st.success(f"✅ New cost center created: **{key_clean}**")
                        st.rerun()
                    else:
                        st.warning(f"Cost center **{key_clean}** already exists.")
                else:
                    st.warning("Enter a cost center key first.")

    # ── end of tab — structure ────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  TAB — THRESHOLDS
    # ──────────────────────────────────────────────────────────────────────────

    with tab_thresholds:
        st.subheader("🎚️ Variance Flagging Thresholds")
        st.caption(
            "Items are flagged during count import when their variance exceeds "
            "either of these thresholds. Lower values = more flags. "
            "These apply to the active cost center."
        )

        thresholds = st.session_state["db_mgmt"]["thresholds"]

        col_each, col_val = st.columns(2)

        with col_each:
            new_flag_each = st.number_input(
                "Flag if |unit variance| exceeds",
                min_value=0,
                max_value=10000,
                value=int(thresholds.get("flag_each", 24)),
                step=1,
                help="Example: 24 means flag if item count differs by more than 24 units.",
                key="threshold_each_input",
            )

        with col_val:
            new_flag_value = st.number_input(
                "Flag if |value variance| exceeds ($)",
                min_value=0.0,
                max_value=100000.0,
                value=float(thresholds.get("flag_value", 50.0)),
                step=5.0,
                format="%.2f",
                help="Example: 50.00 means flag if variance dollar amount exceeds $50.",
                key="threshold_value_input",
            )

        # Preview impact
        st.markdown("---")
        st.caption("**Current thresholds at a glance:**")
        c1, c2 = st.columns(2)
        c1.metric("Unit threshold",  f"± {new_flag_each} units")
        c2.metric("Value threshold", f"± ${new_flag_value:,.2f}")

        if st.button("💾 Save Thresholds", type="primary"):
            st.session_state["db_mgmt"]["thresholds"] = {
                "flag_each":  new_flag_each,
                "flag_value": new_flag_value,
            }
            st.success(
                f"✅ Thresholds saved — "
                f"units: ±{new_flag_each} · value: ±${new_flag_value:,.2f}"
            )

        st.markdown("---")
        st.subheader("ℹ️ What Gets Flagged")
        st.markdown("""
| Condition | Flag Reason |
|---|---|
| Item not found in DB | Item not found in database |
| unit variance > threshold | Unit variance exceeds threshold |
| value variance > threshold | Value variance exceeds threshold |

Flagged items appear in the count import review panel with a 🚩 indicator.
They are **not blocked** from import — the flag is informational.
        """)

    # ── end of tab — thresholds ───────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  TAB — DANGER ZONE
    # ──────────────────────────────────────────────────────────────────────────

    with tab_danger:
        st.error(
            "⚠️ All operations in this section modify or destroy data. "
            "Create a backup before proceeding."
        )

        # ── Clear Quantities ─────────────────────────────────────────────────
        with st.expander("🔢 Clear All Quantities", expanded=False):
            st.warning(
                "Sets **quantity_on_hand = 0** for every active item. "
                "Item records, prices, GL codes, and history are preserved."
            )
            try:
                active_count = db.count_items("active")
                st.caption(f"Will affect **{active_count:,}** active items.")
            except Exception:
                pass

            confirmed_qty = _mgmt("clear_confirmed") and _mgmt("clear_scope") == "quantities"

            if not confirmed_qty:
                if st.button("🔓 Unlock — Clear Quantities",
                             key="unlock_clear_qty", type="secondary"):
                    _set_mgmt("clear_confirmed", True)
                    _set_mgmt("clear_scope",     "quantities")
                    st.rerun()
            else:
                col_go, col_cancel = st.columns(2)
                with col_go:
                    if st.button("🔢 Confirm — Zero All Quantities",
                                 key="confirm_clear_qty", type="primary",
                                 use_container_width=True):
                        try:
                            with db.get_conn() as conn:  # type: ignore
                                cur = conn.cursor()
                                cur.execute(
                                    "UPDATE items SET quantity_on_hand = 0 "
                                    "WHERE record_status = 'active'"
                                )
                                affected = cur.rowcount
                            reset_db_mgmt_confirm(st.session_state)
                            st.success(f"✅ Zeroed quantities on {affected:,} items.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Operation failed: {e}")
                with col_cancel:
                    if st.button("Cancel", key="cancel_clear_qty",
                                 use_container_width=True):
                        reset_db_mgmt_confirm(st.session_state)
                        st.rerun()

        # ── Clear All Items ──────────────────────────────────────────────────
        with st.expander("🗑️ Clear All Items", expanded=False):
            st.error(
                "Removes **all item records** from the database. "
                "GL code mappings, import logs, and cost center config are preserved. "
                "This cannot be undone without a backup."
            )
            try:
                total_count = db.count_items(None)
                st.caption(f"Will delete **{total_count:,}** total item records.")
            except Exception:
                pass

            confirmed_all = _mgmt("clear_confirmed") and _mgmt("clear_scope") == "all_items"

            if not confirmed_all:
                if st.button("🔓 Unlock — Clear All Items",
                             key="unlock_clear_all", type="secondary"):
                    _set_mgmt("clear_confirmed", True)
                    _set_mgmt("clear_scope",     "all_items")
                    st.rerun()
            else:
                col_go, col_cancel = st.columns(2)
                with col_go:
                    if st.button("🗑️ Confirm — Delete All Items",
                                 key="confirm_clear_all", type="primary",
                                 use_container_width=True):
                        try:
                            with db.get_conn() as conn:  # type: ignore
                                cur = conn.cursor()
                                cur.execute("DELETE FROM items")
                                affected = cur.rowcount
                            reset_db_mgmt_confirm(st.session_state)
                            st.success(f"✅ Deleted {affected:,} item records.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Operation failed: {e}")
                with col_cancel:
                    if st.button("Cancel", key="cancel_clear_all",
                                 use_container_width=True):
                        reset_db_mgmt_confirm(st.session_state)
                        st.rerun()

        # ── Full Reset ───────────────────────────────────────────────────────
        with st.expander("☢️ Full Reset", expanded=False):
            st.error(
                "**Wipes the entire database** — all items, history, import logs, "
                "and variance records. GL code mappings are also cleared. "
                "This is irreversible without a backup."
            )
            current_cc = st.session_state["db"].get("cost_center", "default")
            st.caption(
                f"To confirm, type the active cost center name exactly: "
                f"**`{current_cc}`**"
            )

            reset_confirm_text = st.text_input(
                "Type cost center name to unlock",
                key="full_reset_confirm_input",
                placeholder=current_cc,
            )
            unlock_ready = reset_confirm_text.strip() == current_cc.strip()

            if not unlock_ready:
                st.button("☢️ Full Reset", disabled=True,
                          key="full_reset_btn_disabled",
                          help="Type the cost center name above to enable.")
            else:
                confirmed_reset = _mgmt("clear_confirmed") and \
                                  _mgmt("clear_scope") == "full_reset"
                if not confirmed_reset:
                    if st.button("🔓 Unlock Full Reset",
                                 key="unlock_full_reset", type="secondary"):
                        _set_mgmt("clear_confirmed", True)
                        _set_mgmt("clear_scope",     "full_reset")
                        st.rerun()
                else:
                    col_go, col_cancel = st.columns(2)
                    with col_go:
                        if st.button("☢️ Confirm Full Reset",
                                     key="confirm_full_reset", type="primary",
                                     use_container_width=True):
                            try:
                                with db.get_conn() as conn:  # type: ignore
                                    cur = conn.cursor()
                                    cur.execute("DELETE FROM count_variance_detail")
                                    cur.execute("DELETE FROM count_imports")
                                    cur.execute("DELETE FROM item_history")
                                    cur.execute("DELETE FROM price_history")
                                    cur.execute("DELETE FROM import_log")
                                    cur.execute("DELETE FROM items")
                                reset_db_mgmt_confirm(st.session_state)
                                st.success("✅ Full reset complete. Database is empty.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Full reset failed: {e}")
                    with col_cancel:
                        if st.button("Cancel", key="cancel_full_reset",
                                     use_container_width=True):
                            reset_db_mgmt_confirm(st.session_state)
                            st.rerun()

    # ── end of tab — danger zone ──────────────────────────────────────────────

# ── end of page — database management ────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MAIN NAV — UPDATED ROUTER
#  Replace the existing main() function in app.py with this version.
#  Changes from original:
#    1. init_session_state() called once at bootstrap
#    2. db_management and import_mode_selector routes added
#    3. sidebar nav gets Database and Mode Selector entries
# ──────────────────────────────────────────────────────────────────────────────

def main():
    from session_state import init_session_state
    from ui_skeleton   import (build_default_registry, MenuBar,
                                SidebarConfig, DEFAULT_MODE_REG)

    # ── Bootstrap (once per session) ─────────────────────────────────────────
    if "_registry" not in st.session_state:
        st.session_state["_registry"]    = build_default_registry()
    if "_sidebar_cfg" not in st.session_state:
        st.session_state["_sidebar_cfg"] = SidebarConfig()
    if "_mode_reg" not in st.session_state:
        st.session_state["_mode_reg"]    = DEFAULT_MODE_REG

    init_session_state(st.session_state)

    reg         = st.session_state["_registry"]
    sidebar_cfg = st.session_state["_sidebar_cfg"]
    menubar     = MenuBar(reg)

    # ── Inject top nav + footer ───────────────────────────────────────────────
    status_bar.inject_topnav(menubar, sidebar_visible=sidebar_cfg.visible)
    status_bar.inject_footer()

    # ── Determine current page ────────────────────────────────────────────────
    page = st.query_params.get("page", "dashboard")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    if sidebar_cfg.visible:
        with st.sidebar:
            if sidebar_cfg.show_cost_center:
                cc = st.session_state["db"].get("cost_center", "default")
                st.markdown(f"**{sidebar_cfg.custom_label}**")
                st.caption(f"Cost center: `{cc}`")
                st.markdown("---")

            if sidebar_cfg.show_nav:
                nav_items = [
                    ("🏠 Dashboard",      "dashboard",       "dashboard"),
                    ("📦 Inventory",      "inventory",       "inventory"),
                    ("📥 Import",         "import",          "vendor_import"),
                    ("📋 Count Import",   "count_import",    "count_import"),
                    ("🏷️  GL Codes",       "gl_codes",        "gl_codes"),
                    ("📜 History",        "history",         "history"),
                    ("📤 Export",         "export",          "export"),
                    ("🗄️  Database",       "db_management",   "db_management"),
                    ("⚙️  Settings",       "settings",        "settings"),
                ]
                for label, page_key, flag_key in nav_items:
                    if reg.is_enabled(flag_key):
                        if st.button(label, key=f"nav_{page_key}",
                                     use_container_width=True):
                            st.query_params["page"] = page_key
                            st.rerun()

    onedrive_auth_sidebar()

    # ── Module versions ───────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        with st.expander("🔧 Module Versions", expanded=False):
            import importlib
            _modules = [
                "app", "database", "importer",
                "count_importer", "gl_manager",
                "ui_skeleton", "session_state", "status_bar",
                "count_sheet_generator", "page_count_sheets",
            ]
            for _name in _modules:
                try:
                    _mod = importlib.import_module(_name)
                    _ver = getattr(_mod, "__version__", "—")
                except Exception:
                    _ver = "error"
                st.caption(f"`{_name}` — v{_ver}")
    # ── end of module versions ────────────────────────────────────────────────

    # ── Route to page ─────────────────────────────────────────────────────────
    if   page == "dashboard":        page_dashboard()
    elif page == "inventory":        page_inventory()
    elif page == "import":           page_import()
    elif page == "count_import":     page_count_import()
    elif page == "count_overrides":  page_count_overrides()
    elif page == "gl_codes":         page_gl_codes()
    elif page == "history":          page_history()
    elif page == "export":           page_export()
    elif page == "db_management":    page_db_management()
    elif page in ("settings",
                  "settings_sidebar",
                  "settings_prefs"):  page_settings()
    else:                            page_dashboard()


if __name__ == "__main__":
    main()


# ── end of main nav ───────────────────────────────────────────────────────────
