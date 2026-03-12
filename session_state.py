# ──────────────────────────────────────────────────────────────────────────────
#  session_state.py  —  Session State Schema + Initializer
#  Single source of truth for all st.session_state keys used in UHA IMS.
#
#  Call init_session_state() once at the top of app.py before any page
#  renders.  It is idempotent — existing keys are never overwritten, so
#  user state survives Streamlit reruns.
#
#  Schema sections:
#    • IMPORT MODE      — tier, active mode, combined pair, managed pipeline
#    • IMPORT SESSION   — file upload state, parse results, variance records
#    • DATABASE         — active cost center, connection key
#    • UI PREFS         — sidebar visibility, mode selector placement
#    • DB MANAGEMENT    — backup/restore state, threshold config
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from typing import Any, Dict

# ── end of imports ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  DEFAULT STATE TREE
#  All defaults live here.  Keys that should never be overwritten on rerun
#  are seeded via _seed() which skips keys already present.
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULTS: Dict[str, Any] = {

    # ── Import Mode ──────────────────────────────────────────────────────────
    #
    #  tier:     which selector tier is active
    #  active:   the single selected mode (Tier 1)
    #  combined: ordered list of exactly 2 mode keys (Tier 2, same allowed twice)
    #  pipeline: Tier 3 managed mode — flexible steps keyed to order int or None,
    #            fixed steps keyed to bool
    #  display:  where the mode selector UI appears
    #
    "import_mode": {
        "tier":   "single",           # "single" | "combined" | "managed"
        "active": "validation",       # default mode on first load

        "combined": [],               # e.g. ["error_driven", "validation"]

        "pipeline": {
            # ── Flexible steps — value = order int, None = not selected ──────
            "duplicate_detection":    None,
            "error_filter":           None,
            "cluster_by_similarity":  None,
            "panel_build":            None,
            "fix_inline":             None,
            "ai_smart_chooser":       None,

            # ── Cluster sub-options (active when cluster_by_similarity set) ──
            "cluster_options": {
                "description": True,
                "pack_size":   True,
                "vendor":      False,
                "brand":       True,
                "gtin":        False,
                "gl_code":     False,
            },

            # ── Multi-pass registry — maps step key → list of pipeline slots ─
            #   e.g. {"cluster_by_similarity": [3, 7]} means two passes
            "passes": {},

            # ── Fixed steps — checkmark only, order is pipeline-determined ───
            "manual_verification":    False,
            "require_panel_complete": False,
            "revalidate_loop":        False,
        },

        "display": "sidebar",         # "sidebar" | "popup" | "inline"
    },

    # ── Import Session ────────────────────────────────────────────────────────
    #
    #  Tracks the lifecycle of a single file import from upload → commit.
    #  Cleared at the start of each new file upload.
    #
    "import_session": {
        "filename":         None,     # uploaded filename
        "file_hash":        None,     # SHA-256 short digest for dupe detection
        "file_bytes":       None,     # raw bytes (held until commit or cancel)
        "format_info":      {},       # output of detect_format()
        "records":          [],       # List[CountRecord] or List[ImportRecord]
        "variance_records": [],       # List[VarianceRecord] after calculate_variance()
        "analysis":         {},       # output of analyze_import() for vendor imports
        "meta":             {},       # CountImportMeta fields as dict
        "stage":            "idle",   # "idle"|"parsed"|"reviewed"|"committed"
        "errors":           [],       # parse/validation errors
        "warnings":         [],       # non-fatal issues surfaced to user
    },

    # ── Database / Cost Center ────────────────────────────────────────────────
    #
    #  cost_center: the active cost center key used by _get_db()
    #  available:   list of cost center dicts populated on app load
    #
    "db": {
        "cost_center":  "default",
        "available":    [],           # [{key, name, description}]
        "connected":    False,
        "pool_status":  "unknown",    # "ok" | "degraded" | "unknown"
    },

    # ── UI Preferences ────────────────────────────────────────────────────────
    "ui": {
        "sidebar_visible":       True,
        "top_nav_visible":       True,
        "theme":                 "light",    # "light" | "dark"
        "mode_selector_display": "sidebar",  # mirrors import_mode.display
        "show_flagged_only":     False,       # variance table filter
        "show_non_chargeable":   True,
    },

    # ── Database Management ───────────────────────────────────────────────────
    #
    #  State for the upcoming DB management panel:
    #    backup/restore, create/duplicate/replace, naming, cost center
    #    assignment, clear operations, and threshold configuration.
    #
    "db_mgmt": {
        # Backup / restore
        "last_backup_ts":       None,     # ISO datetime string
        "last_backup_label":    None,     # user-assigned label
        "restore_target":       None,     # backup key selected for restore
        "restore_confirmed":    False,

        # Cost center management
        "pending_cc_name":      "",       # new cost center name being typed
        "pending_cc_key":       "",       # derived slug key
        "pending_cc_desc":      "",       # description
        "edit_cc_key":          None,     # cost center being edited

        # Clear operations (non-destructive by default — soft-delete first)
        "clear_scope":          "none",   # "none"|"quantities"|"all_items"|"full_reset"
        "clear_confirmed":      False,
        "clear_preview":        {},       # summary of what will be affected

        # Variance / flagging thresholds (per cost center override possible)
        "thresholds": {
            "flag_each":  24,             # |variance units| > this → flagged
            "flag_value": 50.0,           # |variance $| > this → flagged
        },
    },
}

# ── end of default state tree ─────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  INIT HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _seed(state, key: str, value: Any) -> None:
    """Set state[key] = value only if key is not already present."""
    if key not in state:
        state[key] = value


def _deep_seed(state, key: str, defaults: Dict) -> None:
    """
    Ensure state[key] exists as a dict and seed any missing sub-keys.
    Top-level key is created if absent; existing sub-keys are untouched.
    """
    if key not in state or not isinstance(state[key], dict):
        state[key] = {}
    for subkey, default_val in defaults.items():
        if subkey not in state[key]:
            state[key][subkey] = default_val

# ── end of init helpers ───────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — INIT SESSION STATE
# ──────────────────────────────────────────────────────────────────────────────

def init_session_state(state=None) -> None:
    """
    Seed all UHA IMS session state keys from _DEFAULTS.
    Idempotent — existing keys (including nested sub-keys) are never
    overwritten, so live user state survives Streamlit reruns.

    Call once at the top of app.py:
        from session_state import init_session_state
        init_session_state(st.session_state)

    The `state` parameter accepts st.session_state or a plain dict
    (useful for unit testing without a Streamlit context).
    """
    if state is None:
        import streamlit as st
        state = st.session_state

    for key, default in _DEFAULTS.items():
        if isinstance(default, dict):
            _deep_seed(state, key, default)
        else:
            _seed(state, key, default)

# ── end of public init ────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — RESET IMPORT SESSION
#  Call this when a new file is uploaded to wipe stale parse state.
# ──────────────────────────────────────────────────────────────────────────────

def reset_import_session(state=None) -> None:
    """Clear import_session back to idle defaults without touching other state."""
    if state is None:
        import streamlit as st
        state = st.session_state

    import copy
    state["import_session"] = copy.deepcopy(_DEFAULTS["import_session"])

# ── end of reset import session ───────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — RESET DB MGMT CONFIRMATION FLAGS
#  Call after any destructive operation completes (or is cancelled) to
#  prevent stale confirmation state from persisting across reruns.
# ──────────────────────────────────────────────────────────────────────────────

def reset_db_mgmt_confirm(state=None) -> None:
    """Reset all confirmation flags in db_mgmt without touching other fields."""
    if state is None:
        import streamlit as st
        state = st.session_state

    mgmt = state.get("db_mgmt", {})
    mgmt["restore_confirmed"] = False
    mgmt["clear_confirmed"]   = False
    mgmt["clear_scope"]       = "none"
    mgmt["clear_preview"]     = {}
    state["db_mgmt"] = mgmt

# ── end of reset db mgmt confirm ─────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — GET / SET HELPERS
#  Convenience accessors so app.py doesn't need to know the nesting.
# ──────────────────────────────────────────────────────────────────────────────

def get_import_tier(state=None) -> str:
    if state is None:
        import streamlit as st; state = st.session_state
    return state.get("import_mode", {}).get("tier", "single")


def get_active_mode(state=None) -> str:
    if state is None:
        import streamlit as st; state = st.session_state
    return state.get("import_mode", {}).get("active", "validation")


def set_active_mode(mode_key: str, state=None) -> None:
    if state is None:
        import streamlit as st; state = st.session_state
    state["import_mode"]["tier"]   = "single"
    state["import_mode"]["active"] = mode_key


def get_pipeline(state=None) -> Dict:
    if state is None:
        import streamlit as st; state = st.session_state
    return state.get("import_mode", {}).get("pipeline", {})


def get_thresholds(state=None) -> Dict:
    if state is None:
        import streamlit as st; state = st.session_state
    return state.get("db_mgmt", {}).get("thresholds", {"flag_each": 24, "flag_value": 50.0})


def get_active_cost_center(state=None) -> str:
    if state is None:
        import streamlit as st; state = st.session_state
    return state.get("db", {}).get("cost_center", "default")

# ── end of get / set helpers ──────────────────────────────────────────────────
