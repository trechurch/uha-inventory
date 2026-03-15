"""
UHA Inventory Management System — Streamlit entry point
v4.2.0

Responsibilities of this file:
  - Page config (must be first Streamlit call)
  - Resource factories (db, importer, gl, feature registry)
  - Auth gate
  - Top nav menu bar (HTML/CSS dropdown)
  - Sidebar: user badge, version panel, admin feature toggles
  - Page routing via st.query_params
  - Dependency injection into all page functions

All page-level UI lives in inventory_logic.py and pca_dashboard.py.
"""

__version__ = "4.2.0"

import streamlit as st
import importlib
from datetime import datetime

# ── Page config (must be FIRST Streamlit call) ───────────────────────
st.set_page_config(
    page_title="UHA Inventory",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Core module imports ───────────────────────────────────────────────
from database   import InventoryDatabase
from importer   import InventoryImporter
from gl_manager import GLCodeManager
import onedrive_connector as od
import auth

# ── UI skeleton ───────────────────────────────────────────────────────
from ui_skeleton import build_default_registry, FeatureRegistry, MenuBar, MenuItem

# ── Page imports ──────────────────────────────────────────────────────
from inventory_logic import (
    page_dashboard,
    page_inventory,
    page_import,
    page_gl_codes,
    page_history,
    page_export,
)
from pca_dashboard import render_pca_dashboard


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


@st.cache_resource
def get_registry() -> FeatureRegistry:
    """
    Module-level singleton for feature registry.
    Cached across reruns; admin toggles mutate this object in place
    and persist until the next server restart / redeploy.
    """
    return build_default_registry()


# ────────────────────────────────────────────────────────────────────
# PAGE ROUTING HELPERS
# ────────────────────────────────────────────────────────────────────

def get_current_page() -> str:
    try:
        return st.query_params.get("page", "dashboard")
    except Exception:
        return "dashboard"


def set_page(key: str) -> None:
    try:
        st.query_params["page"] = key
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────
# TOP NAV BAR
# ────────────────────────────────────────────────────────────────────

_NAV_CSS = """
<style>
.uha-nav {
    display: flex;
    flex-direction: row;
    align-items: stretch;
    background: #1a1a2e;
    border-radius: 6px;
    padding: 0 4px;
    margin-bottom: 14px;
}
.uha-nav-title {
    color: #e63946;
    font-weight: 700;
    font-size: 13px;
    padding: 10px 16px 10px 8px;
    letter-spacing: 0.5px;
    white-space: nowrap;
    align-self: center;
    border-right: 1px solid #2d3748;
    margin-right: 4px;
}
.uha-nav-menu {
    position: relative;
    display: inline-block;
}
.uha-nav-btn {
    display: inline-block;
    color: #adb5bd;
    padding: 10px 14px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    text-decoration: none;
    border-radius: 4px;
    white-space: nowrap;
    transition: background 0.12s, color 0.12s;
}
.uha-nav-menu:hover > .uha-nav-btn {
    background: #16213e;
    color: #ffffff;
}
.uha-nav-dropdown {
    display: none;
    position: absolute;
    top: 100%;
    left: 0;
    background: #16213e;
    min-width: 235px;
    z-index: 9999;
    border-radius: 0 4px 4px 4px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.55);
    border: 1px solid #0f3460;
    padding: 4px 0;
}
.uha-nav-menu:hover .uha-nav-dropdown {
    display: block;
}
.uha-nav-item {
    display: block;
    padding: 7px 16px;
    color: #adb5bd;
    text-decoration: none !important;
    font-size: 12.5px;
    white-space: nowrap;
    transition: background 0.1s, color 0.1s;
}
.uha-nav-item:hover {
    background: #0f3460;
    color: #ffffff;
    text-decoration: none !important;
}
.uha-nav-item.uha-active {
    color: #e63946;
    font-weight: 600;
}
.uha-nav-sep {
    border: none;
    border-top: 1px solid #2d3748;
    margin: 4px 8px;
}
</style>
"""


def render_top_nav(registry: FeatureRegistry) -> None:
    """Render HTML/CSS dropdown menu bar. Feature-flagged items are hidden when disabled."""
    menu_bar   = MenuBar(registry)
    cur_page   = get_current_page()

    def item_html(item: MenuItem) -> str:
        if item.separator:
            return '<hr class="uha-nav-sep"/>'
        if not menu_bar.is_item_enabled(item):
            return ""
        lbl = item.full_label
        if item.page_key:
            active = " uha-active" if cur_page == item.page_key else ""
            return f'<a class="uha-nav-item{active}" href="?page={item.page_key}">{lbl}</a>'
        if item.js_action:
            safe = item.js_action.replace('"', "&quot;")
            return f'<a class="uha-nav-item" href="#" onclick="{safe}; return false;">{lbl}</a>'
        return ""

    menus_html = ""
    for menu in menu_bar.menus:
        children = "".join(item_html(c) for c in menu.children)
        menus_html += (
            f'<div class="uha-nav-menu">'
            f'  <span class="uha-nav-btn">{menu.label}</span>'
            f'  <div class="uha-nav-dropdown">{children}</div>'
            f'</div>'
        )

    st.markdown(
        f'{_NAV_CSS}<div class="uha-nav">'
        f'<span class="uha-nav-title">🏟️ UHA IMS</span>'
        f'{menus_html}</div>',
        unsafe_allow_html=True,
    )


# ────────────────────────────────────────────────────────────────────
# VERSION PANEL
# ────────────────────────────────────────────────────────────────────

def render_version_panel() -> None:
    """Sidebar expander showing __version__ for every module."""
    rows = [("app", __version__)]
    for mod_name, mod_import in [
        ("database",        "database"),
        ("importer",        "importer"),
        ("gl_manager",      "gl_manager"),
        ("inventory_logic", "inventory_logic"),
        ("auth",            "auth"),
        ("ui_skeleton",     "ui_skeleton"),
        ("pca_dashboard",   "pca_dashboard"),
        ("pca_engine",      "pca_engine"),
        ("count_importer",  "count_importer"),
        ("status_bar",      "status_bar"),
    ]:
        try:
            mod = importlib.import_module(mod_import)
            ver = getattr(mod, "__version__", "—")
        except Exception:
            ver = "n/a"
        rows.append((mod_name, ver))

    with st.sidebar.expander("📦 Module Versions", expanded=False):
        for name, ver in rows:
            st.caption(f"`{name}` — v{ver}")


# ────────────────────────────────────────────────────────────────────
# SIDEBAR
# ────────────────────────────────────────────────────────────────────

_SIDEBAR_NAV_OPTIONS = [
    ("dashboard",    "🏠 Dashboard"),
    ("inventory",    "📦 Inventory"),
    ("import",       "📥 Vendor Import"),
    ("count_import", "📋 Count Import"),
    ("gl_codes",     "🏷️ GL Codes"),
    ("history",      "📜 History"),
    ("export",       "📤 Export"),
]


def render_sidebar(db, registry: FeatureRegistry) -> None:
    with st.sidebar:
        st.image("https://img.icons8.com/emoji/96/stadium.png", width=52)
        st.markdown("**UHA TDECU Stadium**")
        st.caption("Compass Group · Inventory IMS")
        st.markdown("---")

        # ── User badge ────────────────────────────────────────────────
        auth.render_user_badge()
        st.markdown("---")

        # ── Quick-nav ─────────────────────────────────────────────────
        cur = get_current_page()
        keys    = [k for k, _ in _SIDEBAR_NAV_OPTIONS]
        labels  = [l for _, l in _SIDEBAR_NAV_OPTIONS]
        idx     = keys.index(cur) if cur in keys else 0

        chosen = st.radio(
            "Navigate",
            options=keys,
            format_func=lambda k: dict(_SIDEBAR_NAV_OPTIONS).get(k, k),
            index=idx,
            key="sidebar_nav_radio",
        )
        if chosen != cur:
            set_page(chosen)
            st.rerun()

        # ── PCA (feature-gated) ───────────────────────────────────────
        if registry.is_enabled("pca_engine"):
            if st.button("🧪 PCA Creator", use_container_width=True):
                set_page("pca")
                st.rerun()

        st.markdown("---")
        st.caption("☁️ OneDrive: pending IT approval")
        st.markdown("---")

        # ── Admin: inline feature toggles ─────────────────────────────
        if auth.is_admin():
            with st.expander("⚙️ Feature Toggles (Admin)", expanded=False):
                for feat in registry.all_features():
                    new_val = st.checkbox(
                        feat.description or feat.name,
                        value=feat.option_available,
                        key=f"feat_sb_{feat.name}",
                    )
                    if new_val != feat.option_available:
                        registry.set(feat.name, new_val)
                        st.rerun()

        # ── Version panel ─────────────────────────────────────────────
        render_version_panel()


# ────────────────────────────────────────────────────────────────────
# SETTINGS PAGE
# ────────────────────────────────────────────────────────────────────

def page_settings(db, registry: FeatureRegistry) -> None:
    st.title("⚙️ Settings")

    if not auth.is_admin():
        st.warning("🔒 Admin access required.")
        st.info(
            "The first user to sign in on a fresh installation is automatically "
            "granted admin. Ask your admin to promote your account via User Management."
        )
        return

    tab1, tab2 = st.tabs(["🔧 Feature Toggles", "👥 User Management"])

    with tab1:
        st.subheader("Feature Toggles")
        st.caption(
            "Toggles persist while the app server is running and apply to all users. "
            "They reset to defaults on the next deploy."
        )
        cols = st.columns(2)
        for i, feat in enumerate(registry.all_features()):
            with cols[i % 2]:
                new_val = st.toggle(
                    feat.description or feat.name,
                    value=feat.option_available,
                    key=f"settings_feat_{feat.name}",
                    help=feat.name,
                )
                if new_val != feat.option_available:
                    registry.set(feat.name, new_val)
                    st.rerun()

    with tab2:
        auth.render_user_management(db)


# ────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────

def main() -> None:
    db       = get_db()
    registry = get_registry()

    # ── Auth gate ─────────────────────────────────────────────────────
    if not auth.require_auth(db):
        return

    # ── Top nav ───────────────────────────────────────────────────────
    render_top_nav(registry)

    # ── Sidebar ───────────────────────────────────────────────────────
    render_sidebar(db, registry)

    # ── Dep instances ─────────────────────────────────────────────────
    importer = get_importer()
    gl       = get_gl()

    # ── Page dispatch ─────────────────────────────────────────────────
    page = get_current_page()

    if   page == "dashboard":    page_dashboard(db)
    elif page == "inventory":    page_inventory(db)
    elif page == "import":       page_import(db, importer, od)
    elif page == "count_import":
        try:
            from count_importer import page_count_import
            page_count_import(db)
        except (ImportError, AttributeError):
            st.info("📋 Count Import module is coming soon.")
    elif page == "gl_codes":     page_gl_codes(db, gl, od)
    elif page == "history":      page_history(db)
    elif page == "export":       page_export(db, od)
    elif page == "pca":
        if registry.is_enabled("pca_engine"):
            render_pca_dashboard(db)
        else:
            st.warning("PCA Engine is not enabled. Go to **Settings → Feature Toggles** to enable it.")
    elif page in ("settings", "settings_sidebar", "settings_prefs"):
        page_settings(db, registry)
    else:
        st.info(f"Page `{page}` is not yet implemented.")


if __name__ == "__main__":
    main()
