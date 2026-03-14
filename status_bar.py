# ──────────────────────────────────────────────────────────────────────────────
#  status_bar.py  —  Top Navigation Bar + Footer Injector
#
#  Nav link strategy (critical — read before editing):
#
#    page_key items  →  plain <a href="?page=KEY"> with NO javascript.
#                       Streamlit's iframe does NOT block plain anchor hrefs.
#                       window.top.location.href = "..." IS blocked — never use it.
#
#    js_action items →  onclick="..." for browser-native calls
#                       (window.open, window.print, navigator.share, window.close)
#                       These work because they target the current window, not parent.
#
#    Sidebar toggle  →  JS that finds Streamlit's built-in collapse/expand button
#                       inside window.parent.document and clicks it programmatically.
# ──────────────────────────────────────────────────────────────────────────────

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    from ui_skeleton import MenuBar, MenuItem

# ── end of imports ────────────────────────────────────────────────────────────

__version__ = "3.1.5"


# ──────────────────────────────────────────────────────────────────────────────
#  CSS — variables + nav layout
# ──────────────────────────────────────────────────────────────────────────────

_CSS = """
<style>
/* ── Hide Streamlit's internal decoration span (prevents stray <span> at top) ── */
span[data-testid="stDecoration"] {
    display: none !important;
}

/* Some Streamlit versions inject a plain <span> as the first child of .stApp */
.stApp > span:first-child {
    display: none !important;
}
/* ── Global page adjustments ─────────────────────────────────────────────── */
/* Push Streamlit's default content down so it doesn't hide under the nav bar */
.stApp > header { display: none !important; }
.block-container  { padding-top: 3.6rem !important; }

/* ── Top nav bar ─────────────────────────────────────────────────────────── */
#uha-topnav {
    position:         fixed;
    top:              0;
    left:             0;
    right:            0;
    z-index:          999999;
    height:           2.6rem;
    background:       #1a1a2e;
    border-bottom:    1px solid #2d2d4e;
    display:          flex;
    align-items:      center;
    padding:          0 0.5rem;
    font-family:      'Segoe UI', system-ui, sans-serif;
    font-size:        0.82rem;
    color:            #e0e0f0;
    box-shadow:       0 2px 8px rgba(0,0,0,0.4);
    user-select:      none;
}

/* ── Sidebar toggle button ───────────────────────────────────────────────── */
#uha-sidebar-toggle {
    background:     none;
    border:         none;
    color:          #a0a0c0;
    font-size:      1.1rem;
    cursor:         pointer;
    padding:        0.25rem 0.55rem;
    border-radius:  4px;
    margin-right:   0.4rem;
    line-height:    1;
    transition:     background 0.15s, color 0.15s;
    flex-shrink:    0;
}
#uha-sidebar-toggle:hover {
    background:     rgba(255,255,255,0.1);
    color:          #ffffff;
}

/* ── App brand label ─────────────────────────────────────────────────────── */
#uha-brand {
    font-weight:    700;
    font-size:      0.88rem;
    color:          #c8b8f8;
    letter-spacing: 0.02em;
    margin-right:   0.6rem;
    white-space:    nowrap;
    flex-shrink:    0;
}

/* ── Menu list ───────────────────────────────────────────────────────────── */
#uha-topnav ul.uha-menu {
    display:        flex;
    list-style:     none;
    margin:         0;
    padding:        0;
    height:         100%;
    align-items:    stretch;
}

/* ── Top-level menu item ─────────────────────────────────────────────────── */
#uha-topnav ul.uha-menu > li {
    position:       relative;
    display:        flex;
    align-items:    center;
}

#uha-topnav ul.uha-menu > li > a.uha-top-link {
    display:        flex;
    align-items:    center;
    height:         100%;
    padding:        0 0.75rem;
    color:          #c8c8e8;
    text-decoration: none;
    cursor:         pointer;
    border-radius:  3px;
    transition:     background 0.15s, color 0.15s;
    white-space:    nowrap;
    gap:            0.3rem;
}
#uha-topnav ul.uha-menu > li > a.uha-top-link:hover,
#uha-topnav ul.uha-menu > li:hover > a.uha-top-link {
    background:     rgba(255,255,255,0.08);
    color:          #ffffff;
}
#uha-topnav ul.uha-menu > li > a.uha-top-link .uha-arrow {
    font-size:      0.6rem;
    opacity:        0.6;
    margin-left:    0.15rem;
}

/* ── Dropdown panel ──────────────────────────────────────────────────────── */
#uha-topnav ul.uha-menu > li > .uha-dropdown {
    display:        none;
    position:       absolute;
    top:            100%;
    left:           0;
    min-width:      200px;
    background:     #1e1e3a;
    border:         1px solid #3a3a5e;
    border-radius:  0 4px 4px 4px;
    box-shadow:     0 6px 24px rgba(0,0,0,0.5);
    z-index:        1000000;
    padding:        0.3rem 0;
    list-style:     none;
    margin:         0;
}
#uha-topnav ul.uha-menu > li:hover > .uha-dropdown {
    display:        block;
}

/* ── Dropdown items ──────────────────────────────────────────────────────── */
#uha-topnav .uha-dropdown li > a {
    display:        flex;
    align-items:    center;
    gap:            0.5rem;
    padding:        0.38rem 1rem;
    color:          #c0c0e0;
    text-decoration: none;
    cursor:         pointer;
    font-size:      0.81rem;
    transition:     background 0.12s, color 0.12s;
    white-space:    nowrap;
}
#uha-topnav .uha-dropdown li > a:hover {
    background:     rgba(140, 120, 255, 0.18);
    color:          #ffffff;
}
#uha-topnav .uha-dropdown li > a .uha-icon {
    width:          1.1rem;
    text-align:     center;
    flex-shrink:    0;
    font-size:      0.85rem;
}

/* ── Separator ───────────────────────────────────────────────────────────── */
#uha-topnav .uha-separator {
    height:         1px;
    background:     #2d2d4e;
    margin:         0.3rem 0.7rem;
}

/* ── Right-side spacer + clock ───────────────────────────────────────────── */
.uha-spacer  { flex: 1; }
#uha-clock   {
    font-size:      0.76rem;
    color:          #7070a0;
    padding-right:  0.6rem;
    white-space:    nowrap;
    flex-shrink:    0;
}

/* ── Footer ──────────────────────────────────────────────────────────────── */
#uha-footer {
    font-size:      0.72rem;
    color:          #5a5a7a;
    text-align:     center;
    padding:        0.6rem 0 0.4rem;
    border-top:     1px solid #1e1e3a;
    margin-top:     2rem;
}

</style>
"""


# ──────────────────────────────────────────────────────────────────────────────
#  JS — live clock + sidebar toggle
# ──────────────────────────────────────────────────────────────────────────────

_JS = """
<script>
// ── Live clock ────────────────────────────────────────────────────────────
(function startClock() {
    function tick() {
        const el = document.getElementById('uha-clock');
        if (!el) { setTimeout(tick, 500); return; }
        const now = new Date();
        el.textContent = now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
        setTimeout(tick, 10000);
    }
    tick();
})();

// ── Sidebar toggle ────────────────────────────────────────────────────────
// Finds Streamlit's native collapse button in the parent frame and clicks it.
// Works for both "collapse" (sidebar open) and "expand" (sidebar closed) states.
function uhaToggleSidebar() {
    const p = window.parent ? window.parent.document : document;

    // When sidebar is OPEN  → button lives inside the sidebar header
    // When sidebar is CLOSED → button is the floating expand control
    const selectors = [
        '[data-testid="collapsedControl"]',           // collapsed expand button
        'section[data-testid="stSidebar"] button',    // sidebar's own collapse btn
        '[data-testid="stSidebarCollapseButton"] button',
        'button[aria-label="Close sidebar"]',
        'button[aria-label="Open sidebar"]',
        'button[aria-label="collapse sidebar"]',
        'button[aria-label="expand sidebar"]',
    ];

    for (const sel of selectors) {
        const btn = p.querySelector(sel);
        if (btn) { btn.click(); return; }
    }

    // Last-resort: toggle CSS visibility directly
    const sidebar = p.querySelector('[data-testid="stSidebar"]');
    if (sidebar) {
        const hidden = sidebar.style.display === 'none';
        sidebar.style.display = hidden ? '' : 'none';
    }
}
</script>
"""


# ──────────────────────────────────────────────────────────────────────────────
#  HTML BUILDERS
# ──────────────────────────────────────────────────────────────────────────────

def _item_html(item: "MenuItem") -> str:
    """
    Render a single dropdown <li>.
    - separator → <li class="uha-separator">
    - page_key  → plain <a href="?page=KEY">   (no JS, iframe-safe)
    - js_action → <a href="#" onclick="...">   (browser-native calls)
    - neither   → greyed-out non-link
    """
    if item.separator:
        return '<li><div class="uha-separator"></div></li>'

    icon_html = (
        f'<span class="uha-icon">{item.icon}</span>' if item.icon else
        '<span class="uha-icon"></span>'
    )

    if item.page_key:
        # Pure href — Streamlit's iframe does NOT block these
        href = f"?page={item.page_key}"
        return (
            f'<li><a href="{href}">'
            f'{icon_html}{item.label}'
            f'</a></li>'
        )
    elif item.js_action:
        # JS for browser-native actions — escape single quotes so it embeds safely
        js = item.js_action.replace("'", "\\'").replace('"', '&quot;')
        return (
            f'<li><a href="#" onclick="{js} return false;">'
            f'{icon_html}{item.label}'
            f'</a></li>'
        )
    else:
        # No action — render as disabled label
        return (
            f'<li><a href="#" style="opacity:0.45;cursor:default;" '
            f'onclick="return false;">'
            f'{icon_html}{item.label}'
            f'</a></li>'
        )


def _menu_html(menu: "MenuItem", registry_check) -> str:
    """Render one top-level menu with its dropdown."""
    children_html = ""
    for child in menu.children:
        if child.feature_flag and not registry_check(child.feature_flag):
            continue    # hide feature-flagged items that are off
        children_html += _item_html(child)

    arrow = '<span class="uha-arrow">▾</span>'
    return (
        f'<li>'
        f'  <a class="uha-top-link" href="#">{menu.label}{arrow}</a>'
        f'  <ul class="uha-dropdown">{children_html}</ul>'
        f'</li>'
    )


def _nav_html(menubar: "MenuBar") -> str:
    """Assemble the complete top nav bar HTML."""
    menus_html = ""
    for menu in menubar.menus:
        menus_html += _menu_html(
            menu,
            lambda flag: menubar.registry.is_enabled(flag),
        )

    return f"""
{_CSS}
{_JS}

# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — INJECT TOP NAV
# ──────────────────────────────────────────────────────────────────────────────

def inject_topnav(menubar: "MenuBar", sidebar_visible: bool = True) -> None:
    """
    Call once per page render, before any st.* content calls.
    Injects the fixed top nav bar into the Streamlit app.
    """
    st.markdown(_nav_html(menubar), unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — INJECT FOOTER
# ──────────────────────────────────────────────────────────────────────────────

def inject_footer() -> None:
    st.markdown(
        '<div id="uha-footer">UHA Inventory Management System · Compass Group · TDECU Stadium</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  STATUS BAR CLASS  (timed spinner context manager — used throughout app)
# ──────────────────────────────────────────────────────────────────────────────

class StatusBar:

    @contextmanager
    def timed(self, message: str):
        """
        Context manager: shows a spinner while the block runs, then replaces
        it with a timed success caption.

        Usage:
            with status_bar.timed("Parsing 47 files..."):
                results = do_work()
        """
        placeholder = st.empty()
        placeholder.info(f"⏳ {message}")
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - t0
            placeholder.success(f"✅ {message.rstrip('.')} — done in {elapsed:.2f}s")

    # Keep old call signatures working
    inject_topnav = staticmethod(inject_topnav)
    inject_footer = staticmethod(inject_footer)


# Module-level singleton so callers can do `from status_bar import status_bar`
status_bar = StatusBar()
