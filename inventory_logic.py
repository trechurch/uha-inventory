# inventory_logic.py

# ──────────────────────────────────────────────────────────────────────────────
#  VERSION
# ──────────────────────────────────────────────────────────────────────────────

__version__ = "1.4.0"


"""
Inventory pages extracted from legacy app.py, refactored to be more modular and maintainable. 
Each page is a function that takes the database and other necessary components as arguments. 
This allows us to keep the UI logic separate from the data logic, 
and makes it easier to test and extend in the future.
Zero feature loss, ready to plug into a new modular app.py.
"""

# ── end of version ────────────────────────────────────────────────────────────

from datetime import datetime
from typing import Optional

import io
import pandas as pd
import streamlit as st


# ────────────────────────────────────────────────────────────────────
# DASHBOARD
# ────────────────────────────────────────────────────────────────────

def page_dashboard(db) -> None:
    st.title("🏟️ UHA Inventory — Dashboard")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Items",    db.count_items("active"))
    c2.metric("Total Value",    f"${db.get_inventory_value():,.2f}")
    c3.metric("Low Stock",      len(db.get_low_stock_items()))
    c4.metric("Last Updated",   datetime.now().strftime("%m/%d/%Y"))

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
# INVENTORY LIST + EDITOR  (Updated with click‑to‑edit)
# ────────────────────────────────────────────────────────────────────

def page_inventory(db) -> None:
    st.title("📦 Inventory Items")

    # --- Filters ---
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        search = st.text_input("🔍 Search", placeholder="item name, vendor, GL code...")
    with col2:
        gl_filter = st.text_input("GL Code filter", placeholder="411039")
    with col3:
        show_disc = st.checkbox("Show discontinued")

    # --- Query items ---
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
            "is_chargeable", "cost_center"
        ]
        if c in df.columns
    ]

    st.caption(f"{len(df)} items")

    # --- Click‑to‑select table ---
    st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        key="inv_table"
    )

    # Streamlit stores selected row indices here:
    sel = st.session_state.get("inv_table", {}).get("selection", {}).get("rows", [])

    st.markdown("---")
    st.subheader("✏️ Edit Item")

    if sel:
        row = df.iloc[sel[0]]
        item_key = row["key"]
        item = db.get_item(item_key)
        if item:
            _edit_item_form(db, item)
    else:
        st.info("Select an item from the table above to edit it.")


def _edit_item_form(db, item: dict) -> None:
    with st.form("edit_item"):
        col1, col2, col3 = st.columns(3)

        with col1:
            desc      = st.text_input("Description", value=item.get("description", ""))
            pack_type = st.text_input("Pack Type",   value=item.get("pack_type", ""))
            cost      = st.number_input(
                "Cost",
                value=float(item.get("cost") or 0),
                format="%.4f",
            )
            per       = st.text_input("Per", value=item.get("per", "") or "")

        with col2:
            vendor      = st.text_input("Vendor",      value=item.get("vendor", "") or "")
            item_number = st.text_input("Item #",      value=item.get("item_number", "") or "")
            gl_code     = st.text_input("GL Code",     value=item.get("gl_code", "") or "")
            gl_name     = st.text_input("GL Name",     value=item.get("gl_name", "") or "")

        with col3:
            yield_val  = st.number_input(
                "Yield",
                value=float(item.get("yield") or 1.0),
                format="%.4f",
            )
            conv_ratio = st.number_input(
                "Conv. Ratio",
                value=float(item.get("conv_ratio") or 1.0),
                format="%.4f",
            )
            qoh        = st.number_input(
                "Qty on Hand",
                value=float(item.get("quantity_on_hand") or 0),
                format="%.2f",
            )
            notes      = st.text_area("Notes", value=item.get("user_notes", "") or "")

        st.markdown("**Override Locks** — checked = this field won't be changed by imports")
        oc1, oc2, oc3 = st.columns(3)
        lock_pack  = oc1.checkbox("Lock Pack Type",   value=bool(item.get("override_pack_type")))
        lock_yield = oc2.checkbox("Lock Yield",       value=bool(item.get("override_yield")))
        lock_conv  = oc3.checkbox("Lock Conv. Ratio", value=bool(item.get("override_conv_ratio")))

        submitted = st.form_submit_button("💾 Save Changes")

    if not submitted:
        return

    updates = {
        "description":       desc.strip().upper(),
        "pack_type":         pack_type.strip().upper(),
        "cost":              cost,
        "per":               per,
        "vendor":            vendor,
        "item_number":       item_number,
        "gl_code":           gl_code,
        "gl_name":           gl_name,
        "yield":             yield_val,
        "conv_ratio":        conv_ratio,
        "quantity_on_hand":  qoh,
        "user_notes":        notes,
        "last_updated":      datetime.utcnow(),
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
        item["key"],
        updates,
        change_source="manual_edit",
        changed_by="user",  # later we’ll swap this to auth.get_changed_by()
    )
    st.success("✅ Saved!")
    st.rerun()
