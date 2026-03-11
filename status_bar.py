# ──────────────────────────────────────────────────────────────────────────────
#  status_bar.py  —  Persistent Animated Status / Progress Bar
#
#  Visual:  A fixed footer bar with two layers —
#    • Background:  monospace process-text scrolling horizontally (CSS animation)
#    • Foreground:  a semi-transparent color fill sweeping left → right like a
#                   progress bar, overlaid on the scrolling text
#
#  Usage:
#    from status_bar import StatusBar
#
#    bar = StatusBar()
#    bar.inject()                          # call once per page render in main()
#
#    bar.start("Parsing file...")          # 0 %  — kicks off animation
#    bar.update(0.3, "Reading rows...")    # 30%
#    bar.update(0.7, "Calculating...")     # 70%
#    bar.done("Complete — 1145 items")     # 100%, then fades
#    bar.idle()                            # back to subtle idle pulse
# ──────────────────────────────────────────────────────────────────────────────

import streamlit as st
import streamlit.components.v1 as components

# ── end of imports ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SCROLL TEXT CORPUS  —  cycles through in the background layer
# ──────────────────────────────────────────────────────────────────────────────

_SCROLL_WORDS = (
    "PARSING · FETCHING · NORMALIZING · BUILDING KEY · DIFF · "
    "VARIANCE · BULK FETCH · COMMITTING · WRITING · INDEXING · "
    "MATCHING · GL MAP · PACK TYPE · CONV RATIO · ITEM KEY · "
    "SUPABASE · QUERY · INSERT · UPDATE · ROLLBACK · COMMIT · "
    "CHARGEABLE · LOCATION · SEQ · UOM · PRICE · TOTAL · EACH · "
    "CASE · SEPARATED · COMBINED · FLAGGED · THRESHOLD · HASH · "
    "POOL · CONNECTION · CURSOR · EXECUTE · FETCH · BATCH · "
    "PARSING · FETCHING · NORMALIZING · BUILDING KEY · DIFF · "
)

# ── end of scroll text corpus ─────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  CSS + JS INJECTION  —  fixed footer bar, injected once per app load
# ──────────────────────────────────────────────────────────────────────────────

_INJECT_CSS = """
<style>
  /* ── Fixed footer bar ──────────────────────────────────────────────────── */
  #uha-status-bar {
    position:   fixed;
    bottom:     0;
    left:       0;
    right:      0;
    height:     28px;
    z-index:    9999;
    font-family: 'Courier New', Courier, monospace;
    font-size:  11px;
    overflow:   hidden;
    background: #0e1117;
    border-top: 1px solid #2a2d35;
    display:    flex;
    align-items: center;
  }

  /* ── Scrolling background text ─────────────────────────────────────────── */
  #uha-scroll-text {
    position:   absolute;
    top:        0; left: 0; right: 0; bottom: 0;
    white-space: nowrap;
    color:      #2a4a6a;
    line-height: 28px;
    padding:    0 8px;
    animation:  uha-scroll 18s linear infinite;
  }
  @keyframes uha-scroll {
    0%   { transform: translateX(0); }
    100% { transform: translateX(-50%); }
  }

  /* ── Progress fill overlay ──────────────────────────────────────────────── */
  #uha-progress-fill {
    position:   absolute;
    top:        0; left: 0; bottom: 0;
    width:      0%;
    background: rgba(0, 180, 255, 0.18);
    border-right: 2px solid rgba(0, 180, 255, 0.7);
    transition: width 0.4s ease, background 0.3s ease, border-color 0.3s ease;
    z-index:    1;
  }

  /* ── Status label ────────────────────────────────────────────────────────── */
  #uha-status-label {
    position:   relative;
    z-index:    2;
    padding:    0 12px;
    color:      #8ab4d4;
    font-weight: bold;
    letter-spacing: 0.5px;
    white-space: nowrap;
    overflow:   hidden;
    text-overflow: ellipsis;
    max-width:  60%;
    transition: color 0.3s ease;
  }

  /* ── Right-side timing badge ─────────────────────────────────────────────── */
  #uha-status-timing {
    position:   absolute;
    right:      10px;
    z-index:    2;
    color:      #4a6a8a;
    font-size:  10px;
    letter-spacing: 0.3px;
  }

  /* ── Done state ──────────────────────────────────────────────────────────── */
  .uha-done #uha-progress-fill {
    background:  rgba(0, 220, 130, 0.18);
    border-color: rgba(0, 220, 130, 0.8);
  }
  .uha-done #uha-status-label {
    color: #00dc82;
  }

  /* ── Error state ─────────────────────────────────────────────────────────── */
  .uha-error #uha-progress-fill {
    background:  rgba(255, 80, 80, 0.18);
    border-color: rgba(255, 80, 80, 0.8);
  }
  .uha-error #uha-status-label {
    color: #ff5050;
  }

  /* ── Idle pulse ──────────────────────────────────────────────────────────── */
  .uha-idle #uha-progress-fill {
    animation: uha-idle-pulse 3s ease-in-out infinite;
    border-color: rgba(0, 120, 180, 0.4);
  }
  @keyframes uha-idle-pulse {
    0%, 100% { width: 0%;   opacity: 0.4; }
    50%       { width: 12%;  opacity: 0.8; }
  }

  /* ── Streamlit bottom padding so content isn't hidden behind bar ────────── */
  .main .block-container { padding-bottom: 48px !important; }
</style>

<div id="uha-status-bar" class="uha-idle">
  <div id="uha-scroll-text">{scroll_text}</div>
  <div id="uha-progress-fill"></div>
  <span id="uha-status-label">⬡ UHA IMS ready</span>
  <span id="uha-status-timing"></span>
</div>

<script>
  // Store start time for elapsed display
  window._uhaStart = null;

  window._uhaSet = function(pct, label, state, elapsed) {{
    var bar   = document.getElementById('uha-status-bar');
    var fill  = document.getElementById('uha-progress-fill');
    var lbl   = document.getElementById('uha-status-label');
    var tim   = document.getElementById('uha-status-timing');
    if (!bar) return;

    bar.className = state || '';
    fill.style.width = (pct * 100).toFixed(1) + '%';
    lbl.textContent = label || '';
    tim.textContent = elapsed ? elapsed + 's' : '';
  }};
</script>
"""

# ── end of CSS + JS injection ─────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  STATUS BAR CLASS
# ──────────────────────────────────────────────────────────────────────────────

class StatusBar:
    """
    Manages the fixed-footer status bar.

    Lifecycle per page render:
      bar = StatusBar()
      bar.inject()        # renders the fixed bar HTML once
      bar.start(msg)      # shows active state at 0%
      bar.update(pct, msg)
      bar.done(msg)
      bar.idle()          # back to subtle pulse (call at end of page render)
    """

    # ──────────────────────────────────────────────────────────────────────────
    #  INJECT  —  render the fixed bar into the page (once per render)
    # ──────────────────────────────────────────────────────────────────────────

    def inject(self):
        """
        Render the fixed footer CSS + HTML into the page.
        Must be called near the top of every page function so the bar
        is present regardless of which page is active.
        """
        # Double the scroll text so the loop is seamless
        scroll = (_SCROLL_WORDS + "  " + _SCROLL_WORDS)
        html   = _INJECT_CSS.format(scroll_text=scroll)
        st.markdown(html, unsafe_allow_html=True)
        # Store a JS-update placeholder in session state
        if "_status_ph" not in st.session_state:
            st.session_state["_status_ph"] = st.empty()

    # ── end of inject ─────────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  STATE UPDATES  —  push new state to the bar via JS injection
    # ──────────────────────────────────────────────────────────────────────────

    def _push(self, pct: float, label: str, state: str, elapsed: str = ""):
        """
        Push a state update to the bar by injecting a tiny JS snippet
        that calls window._uhaSet().
        """
        safe_label = label.replace("'", "\\'").replace('"', '\\"')
        js = (
            f"<script>"
            f"(function(){{"
            f"  var t = setInterval(function(){{"
            f"    if(window._uhaSet){{"
            f"      window._uhaSet({pct:.3f}, '{safe_label}', '{state}', '{elapsed}');"
            f"      clearInterval(t);"
            f"    }}"
            f"  }}, 30);"
            f"}})();"
            f"</script>"
        )
        st.markdown(js, unsafe_allow_html=True)

    def start(self, message: str = "Working..."):
        """Kick off — active state at 5%."""
        st.session_state["_status_start"] = __import__("time").perf_counter()
        self._push(0.05, f"⟳  {message}", "")

    def update(self, pct: float, message: str):
        """Update progress (0.0 – 1.0) and label."""
        elapsed = ""
        if "_status_start" in st.session_state:
            secs    = __import__("time").perf_counter() - st.session_state["_status_start"]
            elapsed = f"{secs:.1f}"
        self._push(min(pct, 0.97), f"⟳  {message}", "", elapsed)

    def done(self, message: str = "Done"):
        """Complete — green state at 100%."""
        elapsed = ""
        if "_status_start" in st.session_state:
            secs    = __import__("time").perf_counter() - st.session_state["_status_start"]
            elapsed = f"{secs:.1f}"
            st.session_state.pop("_status_start", None)
        self._push(1.0, f"✓  {message}", "uha-done", elapsed)

    def error(self, message: str = "Error"):
        """Error state — red fill."""
        st.session_state.pop("_status_start", None)
        self._push(1.0, f"✗  {message}", "uha-error")

    def idle(self):
        """Return to subtle idle pulse."""
        st.session_state.pop("_status_start", None)
        self._push(0.0, "⬡  UHA IMS ready", "uha-idle")

    # ── end of state updates ──────────────────────────────────────────────────


# ── end of StatusBar class ────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MODULE-LEVEL SINGLETON  —  import and use directly
# ──────────────────────────────────────────────────────────────────────────────

status_bar = StatusBar()

# ── end of module-level singleton ─────────────────────────────────────────────
