# ──────────────────────────────────────────────────────────────────────────────
#  status_bar.py  —  Top Navigation Bar + Footer Injector
#
#  Injection strategy (Streamlit Cloud compatible):
#
#    st.markdown(html, unsafe_allow_html=True)
#        Renders CSS + nav HTML in the parent frame.
#        <style> tags work. Streamlit strips <script> — don't put scripts here.
#
#    st.components.v1.html(js, height=0)
#        Invisible iframe. Scripts here CAN do window.parent.document
#        (same origin on Streamlit Cloud). Used for sidebar toggle + clock.
#
#    Nav links  = plain <a href="?page=KEY">      — no JS, no iframe restriction
#    JS actions = <a data-js-action="KEY">         — listener attached from iframe
# ──────────────────────────────────────────────────────────────────────────────

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import streamlit as st
import streamlit.components.v1 as components

if TYPE_CHECKING:
    from ui_skeleton import MenuBar, MenuItem

__version__ = "3.2.0"


# ──────────────────────────────────────────────────────────────────────────────
#  CSS
# ──────────────────────────────────────────────────────────────────────────────

_CSS = """<style>
.stApp > header   { display: none !important; }
.block-container  { padding-top: 3.6rem !important; }

#uha-topnav {
    position: fixed; top: 0; left: 0; right: 0; z-index: 999999;
    height: 2.6rem; background: #1a1a2e;
    border-bottom: 1px solid #2d2d4e;
    display: flex; align-items: center; padding: 0 0.5rem;
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 0.82rem; color: #e0e0f0;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4); user-select: none;
}
#uha-sidebar-toggle {
    background: none; border: none; color: #a0a0c0;
    font-size: 1.15rem; cursor: pointer; padding: 0.2rem 0.55rem;
    border-radius: 4px; margin-right: 0.4rem; line-height: 1;
    transition: background 0.15s, color 0.15s; flex-shrink: 0;
}
#uha-sidebar-toggle:hover { background: rgba(255,255,255,0.1); color: #fff; }
#uha-brand {
    font-weight: 700; font-size: 0.88rem; color: #c8b8f8;
    letter-spacing: 0.02em; margin-right: 0.6rem;
    white-space: nowrap; flex-shrink: 0;
}
#uha-topnav ul.uha-menu {
    display: flex; list-style: none; margin: 0; padding: 0;
    height: 100%; align-items: stretch;
}
#uha-topnav ul.uha-menu > li {
    position: relative; display: flex; align-items: center;
}
#uha-topnav ul.uha-menu > li > a.uha-top {
    display: flex; align-items: center; height: 100%;
    padding: 0 0.75rem; color: #c8c8e8; text-decoration: none;
    cursor: pointer; border-radius: 3px;
    transition: background 0.15s, color 0.15s; white-space: nowrap; gap: 0.3rem;
}
#uha-topnav ul.uha-menu > li > a.uha-top:hover,
#uha-topnav ul.uha-menu > li:hover > a.uha-top {
    background: rgba(255,255,255,0.08); color: #fff;
}
.uha-arr { font-size: 0.6rem; opacity: 0.6; margin-left: 0.15rem; }
#uha-topnav ul.uha-menu > li > .uha-dd {
    display: none; position: absolute; top: 100%; left: 0;
    min-width: 210px; background: #1e1e3a;
    border: 1px solid #3a3a5e; border-radius: 0 4px 4px 4px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.5);
    z-index: 1000000; padding: 0.3rem 0; list-style: none; margin: 0;
}
#uha-topnav ul.uha-menu > li:hover > .uha-dd { display: block; }
#uha-topnav .uha-dd li > a {
    display: flex; align-items: center; gap: 0.5rem;
    padding: 0.38rem 1rem; color: #c0c0e0; text-decoration: none;
    cursor: pointer; font-size: 0.81rem;
    transition: background 0.12s, color 0.12s; white-space: nowrap;
}
#uha-topnav .uha-dd li > a:hover { background: rgba(140,120,255,0.18); color: #fff; }
#uha-topnav .uha-dd li > a .ico { width: 1.1rem; text-align: center; flex-shrink: 0; }
.uha-sep { height: 1px; background: #2d2d4e; margin: 0.3rem 0.7rem; }
.uha-spacer { flex: 1; }
#uha-clock  { font-size: 0.76rem; color: #7070a0; padding-right: 0.6rem; white-space: nowrap; flex-shrink: 0; }
#uha-footer {
    font-size: 0.72rem; color: #5a5a7a; text-align: center;
    padding: 0.6rem 0 0.4rem; border-top: 1px solid #1e1e3a; margin-top: 2rem;
}
</style>"""


# ──────────────────────────────────────────────────────────────────────────────
#  COMPANION SCRIPT  (runs in 0-height component iframe, accesses parent DOM)
# ──────────────────────────────────────────────────────────────────────────────

_JS = """<script>
(function () {
  var p = window.parent ? window.parent.document : document;
  var w = window.parent || window;

  /* live clock */
  function tick() {
    var el = p.getElementById('uha-clock');
    if (!el) { setTimeout(tick, 300); return; }
    var now = new Date();
    el.textContent = now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  }
  tick();
  setInterval(tick, 30000);

  /* sidebar toggle */
  function doToggle() {
    var sels = [
      '[data-testid="collapsedControl"]',
      '[data-testid="stSidebarCollapseButton"] button',
      'section[data-testid="stSidebar"] button[kind="header"]',
      'button[aria-label="Close sidebar"]',
      'button[aria-label="Open sidebar"]',
      'button[aria-label="collapse sidebar"]',
      'button[aria-label="expand sidebar"]'
    ];
    for (var i = 0; i < sels.length; i++) {
      var b = p.querySelector(sels[i]);
      if (b) { b.click(); return; }
    }
    /* last resort */
    var sb = p.querySelector('[data-testid="stSidebar"]');
    if (sb) sb.style.display = sb.style.display === 'none' ? '' : 'none';
  }

  function wireToggle() {
    var btn = p.getElementById('uha-sidebar-toggle');
    if (!btn) { setTimeout(wireToggle, 200); return; }
    if (btn._uhaWired) return;
    btn._uhaWired = true;
    btn.addEventListener('click', doToggle);
  }
  wireToggle();

  /* JS-action links */
  var ACTIONS = {
    'new-tab':    function() { w.open(w.location.href, '_blank'); },
    'dup-tab':    function() { w.open(w.location.href, '_blank'); },
    'new-window': function() { w.open(w.location.href, '_blank', 'width=1400,height=900'); },
    'print':      function() { w.print(); },
    'share':      function() {
      var url = w.location.href;
      if (w.navigator.share) { w.navigator.share({title:'UHA Inventory', url:url}); }
      else { w.navigator.clipboard.writeText(url); alert('Link copied to clipboard'); }
    },
    'close-tab':    function() { w.close(); },
    'close-window': function() { w.close(); },
    'exit':         function() { if (w.confirm('Close UHA Inventory?')) w.close(); }
  };

  function wireActions() {
    var links = p.querySelectorAll('[data-jsa]');
    if (!links.length) { setTimeout(wireActions, 300); return; }
    for (var i = 0; i < links.length; i++) {
      (function(el) {
        if (el._uhaWired) return;
        el._uhaWired = true;
        var key = el.getAttribute('data-jsa');
        var fn  = ACTIONS[key];
        if (fn) el.addEventListener('click', function(e){ e.preventDefault(); fn(); });
      })(links[i]);
    }
  }
  wireActions();

})();
</script>"""


# ──────────────────────────────────────────────────────────────────────────────
#  HTML BUILDERS
# ──────────────────────────────────────────────────────────────────────────────

def _item_html(item) -> str:
    if item.separator:
        return '<li><div class="uha-sep"></div></li>'
    ico = f'<span class="ico">{item.icon}</span>' if item.icon else '<span class="ico"></span>'
    if item.page_key:
        return f'<li><a href="?page={item.page_key}">{ico}{item.label}</a></li>'
    elif item.js_action:
        return f'<li><a href="#" data-jsa="{item.js_action}">{ico}{item.label}</a></li>'
    else:
        return f'<li><a href="#" style="opacity:.4;cursor:default;" onclick="return false;">{ico}{item.label}</a></li>'


def _menu_html(menu, is_enabled) -> str:
    kids = "".join(
        _item_html(c) for c in menu.children
        if not c.feature_flag or is_enabled(c.feature_flag)
    )
    return (
        f'<li>'
        f'<a class="uha-top" href="#">{menu.label}<span class="uha-arr">&#9660;</span></a>'
        f'<ul class="uha-dd">{kids}</ul>'
        f'</li>'
    )


def _build_nav_html(menubar) -> str:
    menus = "".join(_menu_html(m, menubar.registry.is_enabled) for m in menubar.menus)
    return (
        _CSS +
        f'<div id="uha-topnav">'
        f'<button id="uha-sidebar-toggle" title="Toggle sidebar">&#9776;</button>'
        f'<span id="uha-brand">&#127DF;&#65039; UHA IMS</span>'
        f'<ul class="uha-menu">{menus}</ul>'
        f'<div class="uha-spacer"></div>'
        f'<span id="uha-clock"></span>'
        f'</div>'
    )


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def inject_topnav(menubar, sidebar_visible: bool = True) -> None:
    """
    Step 1 — CSS + HTML via st.markdown (no scripts).
    Step 2 — JS wiring via components.v1.html height=0 iframe.
    """
    st.markdown(_build_nav_html(menubar), unsafe_allow_html=True)
    components.html(_JS, height=0, scrolling=False)


def inject_footer() -> None:
    st.markdown(
        '<div id="uha-footer">UHA Inventory Management System · Compass Group · TDECU Stadium</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  STATUS BAR CLASS
# ──────────────────────────────────────────────────────────────────────────────

class StatusBar:

    @contextmanager
    def timed(self, message: str):
        ph = st.empty()
        ph.info(f"⏳ {message}")
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - t0
            ph.success(f"✅ {message.rstrip('.')} — done in {elapsed:.2f}s")

    inject_topnav = staticmethod(inject_topnav)
    inject_footer = staticmethod(inject_footer)


status_bar = StatusBar()
