"""
UHA Inventory Management System — Streamlit entry point  (v4.1.0)

This file is intentionally thin:
  - page config
  - resource factories
  - sidebar nav
  - dependency injection into page functions

All page-level UI lives in inventory_logic.py.
"""

import streamlit as st
import pandas as pd
import io
from datetime import datetime

# ── Page config (must be FIRST Streamlit call) ───────────────────────
st.set_page_config(
    page_title="UHA Inventory",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Core module imports ───────────────────────────────────────────────
from database          import InventoryDatabase
from importer          import InventoryImporter
from gl_manager        import GLCodeManager
import onedrive_connector as od

# ── Page imports ──────────────────────────────────────────────────────
from inventory_logic import (
    page_dashboard,
    page_inventory,
    page_import,
    page_gl_codes,
    page_history,
    page_export,
)


# ────────────────────────────────────────────────────────────────────
# RESOURCE FACTORIES
# ────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    return InventoryDatabase()


def get_importer():
    return InventoryImporter(get_db())


def get_gl():
    return GLCodeManager(get_db())


# ────────────────────────────────────────────────────────────────────
# SIDEBAR — OneDrive status
# ────────────────────────────────────────────────────────────────────

def onedrive_auth_sidebar():
    with st.sidebar:
        st.markdown("---")
        st.caption("☁️ OneDrive integration pending IT approval")


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

    db       = get_db()
    importer = get_importer()
    gl       = get_gl()

    if   page == "🏠 Dashboard": page_dashboard(db)
    elif page == "📦 Inventory": page_inventory(db)
    elif page == "📥 Import":    page_import(db, importer, od)
    elif page == "🏷️ GL Codes":  page_gl_codes(db, gl, od)
    elif page == "📜 History":   page_history(db)
    elif page == "📤 Export":    page_export(db, od)


if __name__ == "__main__":
    main()
