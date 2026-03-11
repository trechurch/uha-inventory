# ──────────────────────────────────────────────────────────────────────────────
#  status_bar.py  —  Persistent Animated Status Bar
#
#  Streamlit batches all renders and flushes to the browser only when the
#  Python script finishes — so mid-script JS DOM mutations never fire.
#
#  This version uses TWO layers:
#    1. A fixed CSS footer bar with a continuously scrolling ticker (always on)
#    2. Native st.empty() / st.progress() placeholders for real-time feedback
#       during heavy operations — Streamlit DOES stream these live.
#
#  Usage:
#    from status_bar import status_bar
#
#    status_bar.inject()              # once per page render in main()
#
#    with status_bar.working("Parsing file...") as pb:
#        for i, chunk in enumerate(chunks):
#            process(chunk)
#            pb.progress(i / total, f"Row {i}/{total}")
#
#    # OR for single-shot ops with no loop:
#    status_bar.set_active("Calculating variance...")
#    do_work()
#    status_bar.set_done("Variance complete — 1145 items")
# ──────────────────────────────────────────────────────────────────────────────

import time
import contextlib
import streamlit as st

# ── end of imports ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SCROLL TEXT CORPUS
# ──────────────────────────────────────────────────────────────────────────────

_SCROLL_WORDS = (
    "PARSING · FETCHING · NORMALIZING · BUILDING KEY · VARIANCE DIFF · "
    "BULK FETCH · COMMITTING · WRITING · INDEXING · MATCHING · GL MAP · "
    "PACK TYPE · CONV RATIO · ITEM KEY · SUPABASE · QUERY · INSERT · "
    "UPDATE · ROLLBACK · COMMIT · CHARGEABLE · LOCATION · SEQ · UOM · "
    "PRICE · TOTAL · EACH · CASE · SEPARATED · COMBINED · FLAGGED · "
    "THRESHOLD · HASH · POOL · CONNECTION · CURSOR · EXECUTE · BATCH · "
)

# ── end of scroll text corpus ─────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  CSS — fixed footer ticker (injected once, runs forever client-side)
# ──────────────────────────────────────────────────────────────────────────────

_BAR_CSS = """
<style>
/* ── Fixed footer bar ─────────────────────────────────────────────────────── */
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

/* ── Scrolling ticker text ────────────────────────────────────────────────── */
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

/* ── Active state — brighter ticker + moving highlight sweep ─────────────── */
#uha-status-bar.uha-active #uha-ticker {
  color:     #2a5a8a;
  animation: uha-scroll 8s linear infinite;
}
#uha-status-bar.uha-active::after {
  content:    '';
  position:   absolute;
  top:        0; bottom: 0;
  left:       -40%;
  width:      35%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(0, 160, 255, 0.12) 40%,
    rgba(0, 200, 255, 0.22) 50%,
    rgba(0, 160, 255, 0.12) 60%,
    transparent 100%
  );
  animation: uha-sweep 1.8s ease-in-out infinite;
}
@keyframes uha-sweep {
  0%   { left: -40%; }
  100% { left: 110%; }
}

/* ── Done state ───────────────────────────────────────────────────────────── */
#uha-status-bar.uha-done #uha-ticker {
  color:     #1a5a3a;
  animation: uha-scroll 22s linear infinite;
}
#uha-status-bar.uha-done::after {
  content:    '';
  position:   absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0, 200, 100, 0.07);
  border-top: 1px solid rgba(0, 200, 100, 0.3);
}

/* ── Right-side label ─────────────────────────────────────────────────────── */
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

/* ── Push Streamlit content up so footer doesn't overlap ─────────────────── */
.main .block-container { padding-bottom: 50px !important; }
</style>
"""

_BAR_HTML = """
<div id="uha-status-bar">
  <div id="uha-ticker">SCROLL_TEXT_PLACEHOLDER</div>
  <span id="uha-label">⬡ UHA IMS</span>
</div>
"""

_STATE_JS = """
<script>
(function() {
  var bar = document.getElementById('uha-status-bar');
  var lbl = document.getElementById('uha-label');
  if (!bar) return;
  bar.className = 'STATE_PLACEHOLDER';
  if (lbl) lbl.textContent = 'LABEL_PLACEHOLDER';
})();
</script>
"""

# ── end of CSS ────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  STATUS BAR CLASS
# ──────────────────────────────────────────────────────────────────────────────

class StatusBar:

    # ──────────────────────────────────────────────────────────────────────────
    #  INJECT  —  render the fixed bar into the page once per render cycle
    # ──────────────────────────────────────────────────────────────────────────

    def inject(self):
        """
        Inject the CSS + fixed footer HTML.  Call once at the top of main()
        before any page function runs.
        """
        scroll = _SCROLL_WORDS * 2   # double so loop is seamless
        html   = _BAR_CSS + _BAR_HTML.replace("SCROLL_TEXT_PLACEHOLDER", scroll)
        st.markdown(html, unsafe_allow_html=True)

        # Persistent placeholder for the progress bar area
        # (above the fixed footer, inside the page flow)
        if "_sb_progress_ph" not in st.session_state:
            st.session_state["_sb_progress_ph"] = None

    # ── end of inject ─────────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  STATE SETTERS  —  change bar visual state via injected JS snippet
    #  These fire at end-of-render but are useful for setting persistent state.
    # ──────────────────────────────────────────────────────────────────────────

    def _set_state(self, css_class: str, label: str):
        js = (_STATE_JS
              .replace("STATE_PLACEHOLDER", css_class)
              .replace("LABEL_PLACEHOLDER", label))
        st.markdown(js, unsafe_allow_html=True)

    def set_active(self, label: str = "Working..."):
        self._set_state("uha-active", f"⟳  {label}")

    def set_done(self, label: str = "Done"):
        self._set_state("uha-done", f"✓  {label}")

    def set_idle(self):
        self._set_state("", "⬡  UHA IMS")

    # ── end of state setters ──────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  WORKING CONTEXT MANAGER  —  native st.progress() for real-time feedback
    # ──────────────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def working(self, label: str, total: int = 0):
        """
        Context manager that shows a native Streamlit progress bar + status
        text above the footer during a heavy operation.  These stream live
        to the browser unlike st.markdown() injections.

        Usage:
            with status_bar.working("Parsing...", total=len(rows)) as pb:
                for i, row in enumerate(rows):
                    process(row)
                    pb(i + 1, f"Row {i+1} of {len(rows)}")

        The yielded pb callable: pb(current, message="")
        """
        t0         = time.perf_counter()
        container  = st.empty()
        _total     = total

        class _PB:
            def __init__(self):
                self._last = 0.0

            def __call__(self_, current: int, message: str = ""):
                pct = min(current / _total, 1.0) if _total > 0 else 0.0
                elapsed = time.perf_counter() - t0
                with container.container():
                    st.progress(pct, text=(
                        f"{message}  ·  ⏱ {elapsed:.1f}s" if message
                        else f"⏱ {elapsed:.1f}s"
                    ))
                self_._last = pct

        pb = _PB()

        # Show bar at 0 immediately
        with container.container():
            st.progress(0.0, text=f"⟳  {label}")

        try:
            yield pb
            # On clean exit — show 100% briefly
            elapsed = time.perf_counter() - t0
            with container.container():
                st.progress(1.0, text=f"✓  {label} — {elapsed:.2f}s")
            time.sleep(0.6)
        except Exception:
            with container.container():
                st.progress(pb._last, text=f"✗  Error during: {label}")
            raise
        finally:
            container.empty()   # clean up after done

    # ── end of working context manager ────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  SIMPLE TIMED SPINNER  —  for ops with no row-level progress
    # ──────────────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def timed(self, label: str):
        """
        Simple context manager that shows a progress bar pulsing at indeterminate
        progress, then reports elapsed time on completion.

        Usage:
            with status_bar.timed("Bulk fetch — 1145 items"):
                variance = ci.calculate_variance(records)
        """
        t0        = time.perf_counter()
        container = st.empty()

        # Animate indeterminate progress via a loop in a thread-friendly way.
        # Streamlit doesn't support true indeterminate bars, so we pulse 0→1
        # in steps while the work happens — but since work is synchronous we
        # just show a filled bar with a spinner label.
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

    # ── end of simple timed spinner ───────────────────────────────────────────

# ── end of StatusBar class ────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MODULE-LEVEL SINGLETON
# ──────────────────────────────────────────────────────────────────────────────

status_bar = StatusBar()

# ── end of module-level singleton ─────────────────────────────────────────────
