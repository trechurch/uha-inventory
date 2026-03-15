# app.py — Inventory Management System (modular bridge)

import streamlit as st
import pandas as pd

from database import InventoryDatabase
from gl_manager import GLCodeManager
from importer import InventoryImporter
from pca_engine import PCAEngine
import onedrive_connector as od

from auth import require_auth, render_user_badge
from session_state import init_session_state

from inventory_logic import (
    page_dashboard,
    page_inventory,
    page_import,
    page_gl_codes as page_gl_codes_inventory,
    page_history,
    page_export,
)
from pca_dashboard import page_pca

__version__ = "4.4.0"

# ── Page config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="UHA Inventory",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Resource factories ──────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _get_db() -> InventoryDatabase:
    return InventoryDatabase()

@st.cache_resource(show_spinner=False)
def _get_gl() -> GLCodeManager:
    return GLCodeManager(_get_db())

@st.cache_resource(show_spinner=False)
def _get_importer() -> InventoryImporter:
    return InventoryImporter(_get_db())

@st.cache_resource(show_spinner=False)
def _get_pca() -> PCAEngine:
    return PCAEngine(_get_db())

# ── GL Codes (new fuzzy UI) ─────────────────────────────────────────

def page_gl_codes():
    """GL Code Management Page with Review Queue + advanced settings."""
    st.title("🏷️ GL Code Manager")
    gl = _get_gl()
    db = _get_db()

    # --- SIDEBAR: MATCHING SETTINGS ---
    with st.sidebar:
        st.subheader("⚙️ Matching Settings")
        gl.settings["use_token_matching"] = st.toggle(
            "Token-Based Matching", value=gl.settings["use_token_matching"]
        )
        gl.settings["use_exclusion"] = st.toggle(
            "Pattern Exclusion (Units)", value=gl.settings["use_exclusion"]
        )
        gl.settings["use_weighted_match"] = st.toggle(
            "Weighted 'Best Match'", value=gl.settings["use_weighted_match"]
        )
        gl.settings["min_confidence"] = st.slider(
            "Auto-Assign Confidence Threshold",
            0.0,
            1.0,
            gl.settings["min_confidence"],
        )

    tab1, tab2 = st.tabs(["🤖 Auto-Assign", "📋 Review Queue"])

    with tab1:
        st.subheader("Bulk Auto-Assignment")
        if st.button("Run Auto-Assign on Uncoded Items"):
            results = gl.assign_gl_codes_to_items()
            st.success(f"Successfully assigned {results['assigned']} GL codes.")

    with tab2:
        st.subheader("📋 Review Queue")
        st.caption("Items with mid-confidence matches (0.4 - 0.7).")

        unassigned_items = [i for i in db.get_all_items() if not i.get("gl_code")]
        review_data = []

        for item in unassigned_items:
            match = gl.find_best_gl_match(item.get("description", ""))
            if match and 0.4 <= match["confidence"] < gl.settings["min_confidence"]:
                review_data.append({
                    "key": item["key"],
                    "Item": item["description"],
                    "Suggested GL": f"{match['gl_name']} ({match['gl_code']})",
                    "Confidence": match["confidence"],
                    "Match": match["matched_example"],
                })

        if review_data:
            df_review = pd.DataFrame(review_data)
            edited_df = st.data_editor(df_review, hide_index=True)
            if st.button("Confirm Selected & Save"):
                st.info("Apply selected rows via DB update calls (to be wired).")
        else:
            st.info("Queue is clear. No items requiring review.")

# ── MAIN ROUTER ─────────────────────────────────────────────────────

def main():
    db = _get_db()
    init_session_state()  # keeps your existing session_state behavior

    if not require_auth(db):
        return

    render_user_badge()

    st.sidebar.markdown("---")
    page = st.sidebar.radio(
        "Navigate",
        [
            "Dashboard",
            "Inventory",
            "Import",
            "GL Codes",
            "History",
            "Export",
            "PCA",
        ],
    )

    if page == "Dashboard":
        page_dashboard(db)
    elif page == "Inventory":
        page_inventory(db)
    elif page == "Import":
        page_import(db, _get_importer(), od)
    elif page == "GL Codes":
        # use new advanced GL page, but you can swap to page_gl_codes_inventory if desired
        page_gl_codes()
    elif page == "History":
        page_history(db)
    elif page == "Export":
        page_export(db, od)
    elif page == "PCA":
        page_pca(_get_pca(), db)

if __name__ == "__main__":
    main()
