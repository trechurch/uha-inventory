# inventory_logic.py
# ──────────────────────────────────────────────────────────────────────────────
#  VERSION
# ──────────────────────────────────────────────────────────────────────────────
__version__ = "1.5.0"

"""
All page-level UI functions for UHA IMS.
Dependencies (db, importer, gl, od) are injected by app.py — this module
makes no direct calls to get_db() / cache_resource and imports no Streamlit
globals from app.py.  That keeps each side independently testable.
"""

# ── Standard library ──────────────────────────────────────────────────────────
import io
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Third-party ───────────────────────────────────────────────────────────────
import pandas as pd
import streamlit as st


# ────────────────────────────────────────────────────────────────────
# DASHBOARD
# ────────────────────────────────────────────────────────────────────

def page_dashboard(db) -> None:
    st.title("🏟️ UHA Inventory — Dashboard")

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
            df = pd.DataFrame(low)[
                ["description", "pack_type", "quantity_on_hand", "reorder_point", "vendor"]
            ]
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
            cols = [
                c for c in
                ["description", "pack_type", "cost", "vendor", "last_updated", "status_tag"]
                if c in df.columns
            ]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)


# ────────────────────────────────────────────────────────────────────
# INVENTORY LIST + EDITOR
# ────────────────────────────────────────────────────────────────────

def page_inventory(db) -> None:
    st.title("📦 Inventory Items")

    # ── Filters ──────────────────────────────────────────────────────
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        search = st.text_input("🔍 Search", placeholder="item name, vendor, GL code...")
    with col2:
        gl_filter = st.text_input("GL Code filter", placeholder="411039")
    with col3:
        show_disc = st.checkbox("Show discontinued")

    # ── Fetch ─────────────────────────────────────────────────────────
    if search:
        items = db.search_items(search)
    else:
        items = db.get_all_items("active" if not show_disc else None)

    if gl_filter:
        items = [i for i in items if (i.get("gl_code") or "").startswith(gl_filter)]

    if not items:
        st.info("No items found.")
        return

    df = pd.DataFrame(items)
    display_cols = [
        c for c in [
            "description", "pack_type", "cost", "per", "vendor",
            "gl_code", "gl_name", "status_tag", "quantity_on_hand",
            "is_chargeable",
        ]
        if c in df.columns
    ]

    st.caption(f"{len(df)} items")
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("✏️ Edit Item")

    keys = [i["key"] for i in items]
    selected_key = st.selectbox(
        "Select item to edit", keys,
        format_func=lambda k: k.split("||")[0]
    )

    if selected_key:
        item = db.get_item(selected_key)
        if item:
            _edit_item_form(db, item)


def _edit_item_form(db, item: dict) -> None:
    with st.form("edit_item"):
        col1, col2, col3 = st.columns(3)

        with col1:
            desc      = st.text_input("Description", value=item.get("description", ""))
            pack_type = st.text_input("Pack Type",   value=item.get("pack_type", ""))
            cost      = st.number_input(
                "Cost", value=float(item.get("cost") or 0), format="%.4f"
            )
            per       = st.text_input("Per", value=item.get("per", "") or "")

        with col2:
            vendor      = st.text_input("Vendor",  value=item.get("vendor", "") or "")
            item_number = st.text_input("Item #",  value=item.get("item_number", "") or "")
            gl_code     = st.text_input("GL Code", value=item.get("gl_code", "") or "")
            gl_name     = st.text_input("GL Name", value=item.get("gl_name", "") or "")

        with col3:
            yield_val  = st.number_input(
                "Yield", value=float(item.get("yield") or 1.0), format="%.4f"
            )
            conv_ratio = st.number_input(
                "Conv. Ratio", value=float(item.get("conv_ratio") or 1.0), format="%.4f"
            )
            qoh   = st.number_input(
                "Qty on Hand", value=float(item.get("quantity_on_hand") or 0), format="%.2f"
            )
            notes = st.text_area("Notes", value=item.get("user_notes", "") or "")

        st.markdown("**Override Locks** — checked = this field won't be changed by imports")
        oc1, oc2, oc3 = st.columns(3)
        lock_pack  = oc1.checkbox("Lock Pack Type",   value=bool(item.get("override_pack_type")))
        lock_yield = oc2.checkbox("Lock Yield",       value=bool(item.get("override_yield")))
        lock_conv  = oc3.checkbox("Lock Conv. Ratio", value=bool(item.get("override_conv_ratio")))

        submitted = st.form_submit_button("💾 Save Changes")

    if not submitted:
        return

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

    db._apply_update(
        item["key"], updates,
        change_source="manual_edit",
        changed_by="user",
    )
    st.success("✅ Saved!")
    st.rerun()


# ────────────────────────────────────────────────────────────────────
# IMPORT
# ────────────────────────────────────────────────────────────────────

def page_import(db, importer, od) -> None:
    st.title("📥 Import Files")

    tab1, tab2 = st.tabs(["📤 Upload from Computer", "☁️ Import from OneDrive"])

    # ── Tab 1: upload from local disk ────────────────────────────────
    with tab1:
        st.subheader("Upload Invoice or Inventory CSV")
        uploaded = st.file_uploader(
            "Drop vendor invoice CSV or PAC export here",
            type=["csv", "xlsx"],
            accept_multiple_files=True,
        )

        if uploaded:
            for f in uploaded:
                st.markdown(f"**{f.name}**")
                try:
                    content = f.read()
                    suffix  = ".csv" if f.name.endswith(".csv") else ".xlsx"

                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name

                    df = importer.read_file(tmp_path)
                    os.unlink(tmp_path)

                    if df is None:
                        st.error(f"Could not read: {importer.errors}")
                        continue

                    analysis = importer.analyze_import(df)

                    col1, col2, col3 = st.columns(3)
                    col1.metric("New Items",   len(analysis["new_items"]))
                    col2.metric("Updates",     len(analysis["updates"]))
                    col3.metric("Skipped/Err", len(analysis["skipped"]) + len(analysis["errors"]))

                    if analysis["new_items"]:
                        with st.expander(f"📋 {len(analysis['new_items'])} New Items"):
                            new_df = pd.DataFrame([
                                {"Key": i["key"], "Description": i["description"]}
                                for i in analysis["new_items"]
                            ])
                            st.dataframe(new_df, use_container_width=True, hide_index=True)

                    if analysis["updates"]:
                        with st.expander(f"🔄 {len(analysis['updates'])} Updates"):
                            upd_df = pd.DataFrame([
                                {
                                    "Key": i["key"],
                                    "Fields Changed": ", ".join(i["changes"].keys()),
                                }
                                for i in analysis["updates"]
                            ])
                            st.dataframe(upd_df, use_container_width=True, hide_index=True)

                    if st.button(f"✅ Confirm Import — {f.name}", key=f"confirm_{f.name}"):
                        results = importer.execute_import(
                            analysis,
                            changed_by="web_import",
                            source_document=f.name,
                            doc_date=datetime.now().strftime("%Y-%m-%d"),
                        )
                        st.success(
                            f"Done! {results['new_items_added']} added, "
                            f"{results['items_updated']} updated."
                        )
                        if od.get_access_token():
                            od.archive_file(f.name, content)
                            st.info("📁 Archived to OneDrive.")

                except Exception as e:
                    st.error(f"Error processing {f.name}: {e}")

    # ── Tab 2: pull from OneDrive ─────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────────
# GL CODE MANAGER
# ────────────────────────────────────────────────────────────────────

def page_gl_codes(db, gl, od) -> None:
    st.title("🏷️ GL Code Manager")

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
                adf = pd.DataFrame(results["assignments"])[
                    ["description", "gl_code", "gl_name", "confidence"]
                ]
                st.dataframe(adf, use_container_width=True, hide_index=True)

    with col2:
        st.subheader("GL Code Summary")
        summary = gl.get_gl_summary()
        if summary:
            sdf = pd.DataFrame(summary)
            st.dataframe(sdf, use_container_width=True, hide_index=True)
        else:
            st.info("No GL mappings loaded yet.")


# ────────────────────────────────────────────────────────────────────
# CHANGE HISTORY
# ────────────────────────────────────────────────────────────────────

def page_history(db) -> None:
    st.title("📜 Change History")

    key_input = st.text_input("Enter item key or search term")
    if not key_input:
        return

    history = db.get_item_history(key_input)

    if not history:
        items = db.search_items(key_input)
        if items:
            keys     = [i["key"] for i in items]
            selected = st.selectbox(
                "Select item", keys,
                format_func=lambda k: k.split("||")[0]
            )
            history = db.get_item_history(selected)

    if history:
        df   = pd.DataFrame(history)
        cols = [
            c for c in [
                "change_date", "change_type", "field_changed",
                "old_value", "new_value", "change_source",
                "changed_by", "source_document",
            ]
            if c in df.columns
        ]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)
    else:
        st.info("No history found.")


# ────────────────────────────────────────────────────────────────────
# EXPORT
# ────────────────────────────────────────────────────────────────────

def page_export(db, od) -> None:
    st.title("📤 Export")

    st.subheader("Export Full Inventory")
    items = db.get_all_items()

    if not items:
        st.info("No items to export.")
        return

    df = pd.DataFrame(items)

    # ── Excel ─────────────────────────────────────────────────────────
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

    # ── CSV ───────────────────────────────────────────────────────────
    csv = df.to_csv(index=False)
    st.download_button(
        "⬇️ Download as CSV",
        data=csv,
        file_name=f"inventory_export_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
    st.caption(f"{len(df)} items")

    # ── OneDrive ──────────────────────────────────────────────────────
    if od.get_access_token():
        st.subheader("Save to OneDrive")
        if st.button("☁️ Export to OneDrive"):
            buf = io.BytesIO()
            df.to_excel(buf, index=False)
            filename = f"inventory_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            od.archive_file(filename, buf.getvalue(), subfolder="Exports")
            st.success(f"Saved {filename} to OneDrive Archives.")
