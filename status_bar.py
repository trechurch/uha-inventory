# ──────────────────────────────────────────────────────────────────────────────
#  status_bar.py  —  Top Nav Bar + Footer Status Ticker
#
#  Two injected layers:
#    1. inject_topnav(menubar, registry)  — fixed top bar with CSS dropdown
#       menus.  Disabled items are greyed out + non-interactive.
#       Clicks set st.query_params["page"] to trigger Streamlit reruns.
#    2. inject_footer()  — fixed bottom ticker + native st.progress() helpers
#       for real-time operation feedback.
# ──────────────────────────────────────────────────────────────────────────────

import time
import contextlib
import streamlit as st

# ── end of imports ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  TOP NAV CSS + HTML TEMPLATE
# ──────────────────────────────────────────────────────────────────────────────

_TOPNAV_CSS = """
<style>
/* ── Push Streamlit content down so it clears the nav bar ────────────────── */
.stApp > header { display: none !important; }
.main .block-container {
  padding-top:    54px  !important;
  padding-bottom: 50px  !important;
}
section[data-testid="stSidebar"] > div:first-child {
  padding-top: 54px !important;
}

/* ── Fixed top nav bar ────────────────────────────────────────────────────── */
#uha-topnav {
  position:    fixed;
  top:         0;
  left:        0;
  right:       0;
  height:      42px;
  z-index:     99999;
  background:  #0e1117;
  border-bottom: 1px solid #1e2530;
  display:     flex;
  align-items: center;
  padding:     0 16px;
  gap:         4px;
  font-family: 'Segoe UI', sans-serif;
  font-size:   13px;
  user-select: none;
}

/* ── App title / logo ─────────────────────────────────────────────────────── */
#uha-topnav .uha-brand {
  color:        #5ab4e4;
  font-weight:  700;
  font-size:    14px;
  letter-spacing: 0.5px;
  margin-right: 12px;
  white-space:  nowrap;
}

/* ── Menu trigger buttons ─────────────────────────────────────────────────── */
.uha-menu {
  position:   relative;
  display:    inline-block;
}
.uha-menu-btn {
  background:  transparent;
  border:      none;
  color:       #8ab4d4;
  padding:     6px 10px;
  cursor:      pointer;
  border-radius: 4px;
  font-size:   13px;
  font-family: inherit;
  transition:  background 0.15s, color 0.15s;
}
.uha-menu-btn:hover,
.uha-menu:hover .uha-menu-btn {
  background: #1e2a3a;
  color:      #c8e0f0;
}

/* ── Dropdown panel ───────────────────────────────────────────────────────── */
.uha-dropdown {
  display:       none;
  position:      absolute;
  top:           100%;
  left:          0;
  min-width:     190px;
  background:    #131820;
  border:        1px solid #2a3a4a;
  border-radius: 4px;
  box-shadow:    0 4px 16px rgba(0,0,0,0.5);
  z-index:       100000;
  padding:       4px 0;
}
.uha-menu:hover .uha-dropdown {
  display: block;
}

/* ── Dropdown items ───────────────────────────────────────────────────────── */
.uha-item {
  display:     block;
  padding:     7px 16px;
  color:       #a0c4e0;
  cursor:      pointer;
  white-space: nowrap;
  font-size:   12px;
  text-decoration: none;
  transition:  background 0.1s;
}
.uha-item:hover {
  background: #1e2a3a;
  color:      #e0f0ff;
}

/* ── Disabled items ───────────────────────────────────────────────────────── */
.uha-item.uha-disabled {
  color:          #3a4a5a;
  cursor:         default;
  pointer-events: none;
}
.uha-item.uha-disabled:hover {
  background: transparent;
}

/* ── Separator ────────────────────────────────────────────────────────────── */
.uha-sep {
  height:      1px;
  background:  #1e2a3a;
  margin:      4px 0;
}

/* ── Right-side controls (sidebar toggle, etc.) ───────────────────────────── */
#uha-topnav .uha-right {
  margin-left: auto;
  display:     flex;
  align-items: center;
  gap:         8px;
}
.uha-icon-btn {
  background:  transparent;
  border:      none;
  color:       #5a7a9a;
  font-size:   15px;
  cursor:      pointer;
  padding:     4px 7px;
  border-radius: 4px;
  transition:  background 0.15s, color 0.15s;
}
.uha-icon-btn:hover {
  background: #1e2a3a;
  color:      #8ab4d4;
}
.uha-icon-btn.uha-active {
  color:      #5ab4e4;
  background: #1a2a3a;
}

/* ── Sidebar hide/show ────────────────────────────────────────────────────── */
body.uha-sidebar-hidden section[data-testid="stSidebar"] {
  display: none !important;
}
body.uha-sidebar-hidden .main .block-container {
  max-width: 100% !important;
  padding-left: 2rem !important;
}
</style>
"""

# ── end of top nav CSS ────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  FOOTER CSS + TICKER
# ──────────────────────────────────────────────────────────────────────────────

_SCROLL_WORDS = (
    "PARSING · FETCHING · NORMALIZING · BUILDING KEY · VARIANCE DIFF · "
    "BULK FETCH · COMMITTING · WRITING · INDEXING · MATCHING · GL MAP · "
    "PACK TYPE · CONV RATIO · ITEM KEY · SUPABASE · QUERY · INSERT · "
    "UPDATE · ROLLBACK · COMMIT · CHARGEABLE · LOCATION · SEQ · UOM · "
    "PRICE · TOTAL · EACH · CASE · SEPARATED · COMBINED · FLAGGED · "
    "THRESHOLD · HASH · POOL · CONNECTION · CURSOR · EXECUTE · BATCH · "
)

_FOOTER_CSS = """
<style>
#uha-status-bar {
  position:    fixed;
  bottom:      0;
  left:        0;
  right:       0;
  height:      26px;
  z-index:     9999;
  overflow:    hidden;
  background:  #0e1117;
  border-top:  1px solid #1e2530;
  display:     flex;
  align-items: center;
}
#uha-ticker {
  position:    absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  white-space: nowrap;
  font-family: 'Courier New', monospace;
  font-size:   10px;
  color:       #1a3550;
  line-height: 26px;
  padding:     0 8px;
  animation:   uha-scroll 22s linear infinite;
  user-select: none;
}
@keyframes uha-scroll {
  0%   { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
#uha-status-bar.uha-active #uha-ticker {
  color:     #2a5a8a;
  animation: uha-scroll 8s linear infinite;
}
#uha-status-bar.uha-active::after {
  content:    '';
  position:   absolute;
  top: 0; bottom: 0;
  left: -40%;
  width: 35%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(0,160,255,0.12) 40%,
    rgba(0,200,255,0.22) 50%,
    rgba(0,160,255,0.12) 60%,
    transparent 100%
  );
  animation: uha-sweep 1.8s ease-in-out infinite;
}
@keyframes uha-sweep {
  0%   { left: -40%; }
  100% { left: 110%; }
}
#uha-status-bar.uha-done #uha-ticker { color: #1a5a3a; }
#uha-status-bar.uha-done::after {
  content:  '';
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,200,100,0.07);
}
#uha-label {
  position:    absolute;
  right:       10px;
  font-family: 'Courier New', monospace;
  font-size:   10px;
  color:       #3a6a9a;
  z-index:     2;
  white-space: nowrap;
}
#uha-status-bar.uha-active #uha-label { color: #5ab4e4; }
#uha-status-bar.uha-done   #uha-label { color: #00dc82; }
</style>
"""

_FOOTER_HTML = """
<div id="uha-status-bar">
  <div id="uha-ticker">TICKER_PLACEHOLDER</div>
  <span id="uha-label">⬡ UHA IMS</span>
</div>
"""

# ── end of footer CSS ─────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  STATUS BAR CLASS
# ──────────────────────────────────────────────────────────────────────────────

class StatusBar:

    # ──────────────────────────────────────────────────────────────────────────
    #  INJECT TOP NAV  —  render top bar with menus from MenuBar + registry
    # ──────────────────────────────────────────────────────────────────────────

    def inject_topnav(self, menubar, sidebar_visible: bool = True):
        """
        Render the fixed top nav bar.
        menubar: ui_skeleton.MenuBar instance
        sidebar_visible: current state of the sidebar toggle
        """
        menu_html = self._build_menu_html(menubar)
        sidebar_btn_class = "uha-icon-btn uha-active" if sidebar_visible else "uha-icon-btn"
        sidebar_js = (
            "document.body.classList.toggle('uha-sidebar-hidden');"
            "this.classList.toggle('uha-active');"
        )

        html = _TOPNAV_CSS + f"""
<div id="uha-topnav">
  <span class="uha-brand">⬡ UHA IMS</span>
  {menu_html}
  <div class="uha-right">
    <button class="{sidebar_btn_class}" onclick="{sidebar_js}"
            title="Toggle sidebar">◀</button>
  </div>
</div>
"""
        st.markdown(html, unsafe_allow_html=True)

    def _build_menu_html(self, menubar) -> str:
        parts = []
        for menu in menubar.menus:
            items_html = ""
            for item in menu.children:
                if item.separator:
                    items_html += '<div class="uha-sep"></div>'
                    continue
                enabled   = menubar.is_item_enabled(item)
                css_class = "uha-item" if enabled else "uha-item uha-disabled"
                if enabled and item.page_key:
                    onclick = (
                        f"window.location.href = "
                        f"window.location.pathname + "
                        f"'?page={item.page_key}';"
                    )
                    items_html += (
                        f'<a class="{css_class}" onclick="{onclick}" href="#">'
                        f'{item.full_label}</a>'
                    )
                else:
                    items_html += (
                        f'<span class="{css_class}">'
                        f'{item.full_label}</span>'
                    )
            parts.append(
                f'<div class="uha-menu">'
                f'  <button class="uha-menu-btn">{menu.label} ▾</button>'
                f'  <div class="uha-dropdown">{items_html}</div>'
                f'</div>'
            )
        return "\n".join(parts)

    # ── end of inject top nav ─────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  INJECT FOOTER  —  render the bottom ticker bar
    # ──────────────────────────────────────────────────────────────────────────

    def inject_footer(self):
        """Render the fixed footer ticker. Call once per page render."""
        scroll = _SCROLL_WORDS * 2
        html   = _FOOTER_CSS + _FOOTER_HTML.replace("TICKER_PLACEHOLDER", scroll)
        st.markdown(html, unsafe_allow_html=True)

    def inject(self, menubar=None, sidebar_visible: bool = True):
        """
        Convenience: inject both top nav and footer in one call.
        If menubar is None, only the footer is injected.
        """
        if menubar is not None:
            self.inject_topnav(menubar, sidebar_visible=sidebar_visible)
        self.inject_footer()

    # ── end of inject footer ──────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  WORKING CONTEXT MANAGER  —  native st.progress() for real-time feedback
    # ──────────────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def working(self, label: str, total: int = 0):
        """
        Context manager with a yielded progress callback.

        Usage:
            with status_bar.working("Parsing...", total=len(rows)) as pb:
                for i, row in enumerate(rows):
                    process(row)
                    pb(i + 1, f"Row {i+1}")
        """
        t0        = time.perf_counter()
        container = st.empty()
        _total    = total

        class _PB:
            def __init__(self):
                self._last = 0.0
            def __call__(self_, current: int, message: str = ""):
                pct     = min(current / _total, 1.0) if _total > 0 else 0.0
                elapsed = time.perf_counter() - t0
                with container.container():
                    st.progress(pct, text=(
                        f"{message}  ·  ⏱ {elapsed:.1f}s"
                        if message else f"⏱ {elapsed:.1f}s"
                    ))
                self_._last = pct

        pb = _PB()
        with container.container():
            st.progress(0.0, text=f"⟳  {label}")

        try:
            yield pb
            elapsed = time.perf_counter() - t0
            with container.container():
                st.progress(1.0, text=f"✓  {label} — {elapsed:.2f}s")
            time.sleep(0.6)
        except Exception:
            with container.container():
                st.progress(pb._last, text=f"✗  Error during: {label}")
            raise
        finally:
            container.empty()

    # ── end of working context manager ────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  TIMED CONTEXT MANAGER  —  indeterminate progress for single-shot ops
    # ──────────────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def timed(self, label: str):
        """
        Shows a progress bar at 15% (indeterminate) during work,
        then snaps to 100% with elapsed time on completion.
        """
        t0        = time.perf_counter()
        container = st.empty()
        with container.container():
            st.progress(0.15, text=f"⟳  {label}")
        try:
            yield
            elapsed = time.perf_counter() - t0
            with container.container():
                st.progress(1.0, text=f"✓  {label} — {elapsed:.2f}s")
            time.sleep(0.5)
        except Exception:
            with container.container():
                st.progress(0.15, text=f"✗  Error: {label}")
            raise
        finally:
            container.empty()

    # ── end of timed context manager ──────────────────────────────────────────

# ── end of StatusBar class ────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MODULE-LEVEL SINGLETON
# ──────────────────────────────────────────────────────────────────────────────

status_bar = StatusBar()

# ── end of module-level singleton ─────────────────────────────────────────────
