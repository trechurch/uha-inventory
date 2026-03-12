# ──────────────────────────────────────────────────────────────────────────────
#  ui_skeleton.py  —  Feature Registry, Menu Definitions, Sidebar Config,
#                     Import Mode Registry + Selector Config
#  Ported from Tkinter prototype + extended for Streamlit deployment
# ──────────────────────────────────────────────────────────────────────────────

from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Any

# ── end of imports ────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
#  VERSION
# ──────────────────────────────────────────────────────────────────────────────

__version__ = "3.0.0"

# ── end of version ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  FEATURE TOGGLE + REGISTRY
#  Unchanged from Tkinter prototype — no Streamlit dependency here.
#  option_available=False → item renders greyed out and non-interactive.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FeatureToggle:
    name:             str
    option_available: bool = True
    description:      str  = ""


class FeatureRegistry:

    def __init__(self):
        self.features: Dict[str, FeatureToggle] = {}

    def add(self, name: str, description: str = "", default: bool = True):
        self.features[name] = FeatureToggle(name, default, description)

    def is_enabled(self, name: str) -> bool:
        return self.features.get(
            name, FeatureToggle(name, False)
        ).option_available

    def set(self, name: str, value: bool):
        if name in self.features:
            self.features[name].option_available = value

    def all_features(self) -> List[FeatureToggle]:
        return list(self.features.values())

# ── end of feature toggle + registry ─────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MENU ITEM  —  extended from prototype for Streamlit top-nav
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class MenuItem:
    label:        str
    page_key:     str              = ""      # st.query_params target
    icon:         str              = ""      # emoji prefix
    action:       Optional[Callable] = None  # legacy Tkinter hook
    feature_flag: str              = ""      # registry key; "" = always on
    separator:    bool             = False   # render a divider before this item
    children:     List["MenuItem"] = field(default_factory=list)  # submenu

    @property
    def full_label(self) -> str:
        return f"{self.icon}  {self.label}" if self.icon else self.label

# ── end of menu item ──────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MENU BAR DEFINITION
#  Single source of truth for all top-nav menus and their items.
#  Items with feature_flag set to a disabled feature render greyed out.
# ──────────────────────────────────────────────────────────────────────────────

class MenuBar:

    def __init__(self, registry: FeatureRegistry):
        self.registry = registry
        self.menus: List[MenuItem] = self._build()

    def _build(self) -> List[MenuItem]:
        return [
            # ── File ─────────────────────────────────────────────────────────
            MenuItem(label="File", children=[
                MenuItem("Export Inventory", page_key="export",
                         icon="📤", feature_flag="export"),
                MenuItem("", separator=True),
                MenuItem("Settings",         page_key="settings",
                         icon="⚙️",  feature_flag="settings"),
            ]),

            # ── Inventory ────────────────────────────────────────────────────
            MenuItem(label="Inventory", children=[
                MenuItem("Items",    page_key="inventory",
                         icon="📦", feature_flag="inventory"),
                MenuItem("History",  page_key="history",
                         icon="📜", feature_flag="history"),
                MenuItem("GL Codes", page_key="gl_codes",
                         icon="🏷️",  feature_flag="gl_codes"),
            ]),

            # ── Import ───────────────────────────────────────────────────────
            MenuItem(label="Import", children=[
                MenuItem("Vendor Invoice",  page_key="import",
                         icon="📥", feature_flag="vendor_import"),
                MenuItem("Count Import",    page_key="count_import",
                         icon="📋", feature_flag="count_import"),
                MenuItem("Compare Files",   page_key="compare_counts",
                         icon="📊", feature_flag="compare_counts"),
                MenuItem("Override & Rule Manager", page_key="count_overrides",
                         icon="⚙️"),
                MenuItem("", separator=True),
                MenuItem("Import Mode...",  page_key="import_mode_selector",
                         icon="🧩", feature_flag="import_mode_selector"),
            ]),

            # ── Tools (future — all gated) ───────────────────────────────────
            MenuItem(label="Tools", children=[
                MenuItem("PCA Creator",       page_key="pca",
                         icon="🧪", feature_flag="pca_engine"),
                MenuItem("Transfer Sheet",    page_key="transfer",
                         icon="🔀", feature_flag="transfer_engine"),
                MenuItem("Waste Tracking",    page_key="waste",
                         icon="♻️",  feature_flag="pca_waste_calc"),
                MenuItem("Historical Inject", page_key="historical",
                         icon="🕰️",  feature_flag="mode_historical"),
                MenuItem("In-House Promo",    page_key="promo",
                         icon="🏠", feature_flag="pca_recursive"),
            ]),

            # ── Settings ─────────────────────────────────────────────────────
            MenuItem(label="Settings", children=[
                MenuItem("Feature Toggles", page_key="settings",
                         icon="🔧", feature_flag="settings"),
                MenuItem("Sidebar",         page_key="settings_sidebar",
                         icon="◀️",  feature_flag="settings"),
                MenuItem("Preferences",     page_key="settings_prefs",
                         icon="👤", feature_flag="settings"),
                MenuItem("", separator=True),
                MenuItem("Database...",     page_key="db_management",
                         icon="🗄️",  feature_flag="db_management"),
            ]),
        ]

    def is_item_enabled(self, item: MenuItem) -> bool:
        if not item.feature_flag:
            return True
        return self.registry.is_enabled(item.feature_flag)

# ── end of menu bar definition ────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SIDEBAR CONFIG
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SidebarConfig:
    visible:          bool = True
    show_nav:         bool = True    # show page quick-links
    show_cost_center: bool = True    # show active cost center badge
    show_recent:      bool = True    # show recent imports list
    show_mode_widget: bool = True    # show import mode selector widget
    custom_label:     str  = "UHA TDECU Stadium"

# ── end of sidebar config ─────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  IMPORT MODE — PIPELINE STEP DESCRIPTOR
#
#  Describes a single composable step in the import pipeline.
#  Used by ModeRegistry and the managed-mode grid renderer.
#
#    timing:     "flexible" → user assigns order number
#                "fixed"    → always runs at its natural pipeline position,
#                             renders as a checkbox only
#    repeatable: True → selecting again offers a second-pass slot rather
#                       than deselecting
#    sub_options: optional expandable options (e.g. cluster similarity axes)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineStep:
    key:         str
    label:       str
    icon:        str  = ""
    timing:      str  = "flexible"    # "flexible" | "fixed"
    repeatable:  bool = False
    description: str  = ""
    sub_options: List[Dict[str, Any]] = field(default_factory=list)

# ── end of pipeline step descriptor ──────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  IMPORT MODE DESCRIPTOR
#
#  One entry for each of the 5 named modes.
#  pipeline_steps lists which managed-mode steps this mode contributes
#  when it is part of a Combined or Managed pipeline.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ImportMode:
    key:             str
    label:           str
    icon:            str  = ""
    description:     str  = ""
    pipeline_steps:  List[str] = field(default_factory=list)  # PipelineStep keys

# ── end of import mode descriptor ────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MODE REGISTRY
#  Single source of truth for all named modes and all pipeline steps.
#  The UI reads from here — adding a new mode or step means editing this
#  file only, not hunting through page code.
# ──────────────────────────────────────────────────────────────────────────────

class ModeRegistry:

    def __init__(self):
        self.modes:  Dict[str, ImportMode]   = {}
        self.steps:  Dict[str, PipelineStep] = {}
        self._build_steps()
        self._build_modes()

    # ──────────────────────────────────────────────────────────────────────────
    #  PIPELINE STEPS
    # ──────────────────────────────────────────────────────────────────────────

    def _build_steps(self):
        steps = [

            # ── Flexible steps (user assigns order) ──────────────────────────

            PipelineStep(
                key         = "duplicate_detection",
                label       = "Duplicate Detection",
                icon        = "🔍",
                timing      = "flexible",
                repeatable  = False,
                description = "Find exact and near-duplicate records before ingestion.",
            ),
            PipelineStep(
                key         = "error_filter",
                label       = "Error Filter",
                icon        = "🚨",
                timing      = "flexible",
                repeatable  = False,
                description = "Show only rows with missing fields, bad values, or formatting issues.",
            ),
            PipelineStep(
                key         = "cluster_by_similarity",
                label       = "Cluster by Similarity",
                icon        = "🧩",
                timing      = "flexible",
                repeatable  = True,
                description = "Group similar items for bulk resolution.",
                sub_options = [
                    {"key": "description", "label": "Description",  "default": True},
                    {"key": "pack_size",   "label": "Pack Size",    "default": True},
                    {"key": "vendor",      "label": "Vendor",       "default": False},
                    {"key": "brand",       "label": "Brand",        "default": True},
                    {"key": "gtin",        "label": "GTIN",         "default": False},
                    {"key": "gl_code",     "label": "GL Code",      "default": False},
                ],
            ),
            PipelineStep(
                key         = "panel_build",
                label       = "Dynamic Panel Build",
                icon        = "📐",
                timing      = "flexible",
                repeatable  = False,
                description = "Build issue-specific panels (missing pack type, GL, vendor, etc.).",
            ),
            PipelineStep(
                key         = "fix_inline",
                label       = "Fix Errors Inline",
                icon        = "✏️",
                timing      = "flexible",
                repeatable  = True,
                description = "Let the user correct flagged rows directly in the review table.",
            ),
            PipelineStep(
                key         = "ai_smart_chooser",
                label       = "AI Smart Chooser",
                icon        = "🤖",
                timing      = "flexible",
                repeatable  = False,
                description = "AI-assisted resolution of ambiguous items, pack types, and GL codes.",
            ),

            # ── Fixed steps (checkbox only, natural pipeline position) ────────

            PipelineStep(
                key         = "manual_verification",
                label       = "Manual Verification of Flagged Items",
                icon        = "👁️",
                timing      = "fixed",
                repeatable  = False,
                description = "Pause after analysis — user must review and approve flagged records.",
            ),
            PipelineStep(
                key         = "require_panel_complete",
                label       = "Require Panel Completion",
                icon        = "✅",
                timing      = "fixed",
                repeatable  = False,
                description = "User must resolve all dynamic panels before the import can proceed.",
            ),
            PipelineStep(
                key         = "revalidate_loop",
                label       = "Re-Validate After Inline Fixes",
                icon        = "🔄",
                timing      = "fixed",
                repeatable  = False,
                description = "After inline edits, re-run validation. Loop until clean.",
            ),
        ]
        for s in steps:
            self.steps[s.key] = s

    # ── end of pipeline steps ─────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  NAMED MODES
    # ──────────────────────────────────────────────────────────────────────────

    def _build_modes(self):
        modes = [
            ImportMode(
                key             = "validation",
                label           = "Validation Mode",
                icon            = "🌐",
                description     = "Load → analyze → review all records → approve → write.",
                pipeline_steps  = [
                    "duplicate_detection",
                    "manual_verification",
                    "require_panel_complete",
                ],
            ),
            ImportMode(
                key             = "selection",
                label           = "Selection Mode",
                icon            = "🧭",
                description     = "Choose exactly which rows and fields to import.",
                pipeline_steps  = [
                    "duplicate_detection",
                    "manual_verification",
                ],
            ),
            ImportMode(
                key             = "data_driven",
                label           = "Data-Driven Mode",
                icon            = "📊",
                description     = "The data decides what panels the user sees.",
                pipeline_steps  = [
                    "panel_build",
                    "require_panel_complete",
                ],
            ),
            ImportMode(
                key             = "cluster",
                label           = "Cluster-Driven Mode",
                icon            = "🧩",
                description     = "Group similar items → resolve in bulk.",
                pipeline_steps  = [
                    "duplicate_detection",
                    "cluster_by_similarity",
                    "manual_verification",
                ],
            ),
            ImportMode(
                key             = "error_driven",
                label           = "Error-Driven Mode",
                icon            = "🚨",
                description     = "Show only what's broken. Fix inline. Re-validate.",
                pipeline_steps  = [
                    "error_filter",
                    "fix_inline",
                    "revalidate_loop",
                ],
            ),
        ]
        for m in modes:
            self.modes[m.key] = m

    # ── end of named modes ────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  ACCESSORS
    # ──────────────────────────────────────────────────────────────────────────

    def mode_list(self) -> List[ImportMode]:
        """All modes in display order."""
        return list(self.modes.values())

    def step_list(self) -> List[PipelineStep]:
        """All pipeline steps — flexible first, then fixed."""
        return (
            [s for s in self.steps.values() if s.timing == "flexible"] +
            [s for s in self.steps.values() if s.timing == "fixed"]
        )

    def flexible_steps(self) -> List[PipelineStep]:
        return [s for s in self.steps.values() if s.timing == "flexible"]

    def fixed_steps(self) -> List[PipelineStep]:
        return [s for s in self.steps.values() if s.timing == "fixed"]

    def steps_for_mode(self, mode_key: str) -> List[PipelineStep]:
        """Return the PipelineStep objects associated with a named mode."""
        mode = self.modes.get(mode_key)
        if not mode:
            return []
        return [self.steps[k] for k in mode.pipeline_steps if k in self.steps]

# ── end of mode registry ──────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  IMPORT MODE SELECTOR CONFIG
#  Carries the user's current selector preferences for the UI renderer.
#  Populated from session_state["import_mode"] at render time.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ImportModeSelectorConfig:
    tier:    str  = "single"      # "single" | "combined" | "managed"
    display: str  = "sidebar"     # "sidebar" | "popup" | "inline"
    kiss:    bool = False         # True = hide managed-mode complexity, show Tier 1

    @classmethod
    def from_session(cls, state: Dict) -> "ImportModeSelectorConfig":
        """Build from the import_mode section of session state."""
        im = state.get("import_mode", {})
        return cls(
            tier    = im.get("tier",    "single"),
            display = im.get("display", "sidebar"),
            kiss    = False,
        )

# ── end of import mode selector config ───────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  DATABASE MANAGEMENT CONFIG
#  Carries the set of operations available in the DB management panel.
#  Each entry: key, label, icon, requires_confirm, destructive
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DbOperation:
    key:              str
    label:            str
    icon:             str  = ""
    requires_confirm: bool = False
    destructive:      bool = False
    description:      str  = ""


DB_OPERATIONS: List[DbOperation] = [
    DbOperation("backup",         "Backup Database",
                icon="💾", requires_confirm=False, destructive=False,
                description="Create a named snapshot of the current database state."),
    DbOperation("restore",        "Restore from Backup",
                icon="⏪", requires_confirm=True,  destructive=True,
                description="Replace current data with a previous backup. Cannot be undone."),
    DbOperation("duplicate",      "Duplicate Database",
                icon="📋", requires_confirm=False, destructive=False,
                description="Clone the current database to a new cost center."),
    DbOperation("create_new",     "Create New Database",
                icon="➕", requires_confirm=False, destructive=False,
                description="Create a fresh empty database for a new cost center."),
    DbOperation("rename",         "Rename / Relabel",
                icon="✏️", requires_confirm=False, destructive=False,
                description="Change the display name or label of the active database."),
    DbOperation("assign_cc",      "Assign Cost Center(s)",
                icon="🏷️", requires_confirm=False, destructive=False,
                description="Link this database to one or more cost center keys."),
    DbOperation("set_thresholds", "Set Variance Thresholds",
                icon="🎚️", requires_confirm=False, destructive=False,
                description="Configure flag_each and flag_value thresholds per cost center."),
    DbOperation("clear_qty",      "Clear All Quantities",
                icon="🔢", requires_confirm=True,  destructive=True,
                description="Reset all quantity_on_hand values to 0. Item records are preserved."),
    DbOperation("clear_all",      "Clear All Items",
                icon="🗑️", requires_confirm=True,  destructive=True,
                description="Remove all item records. GL codes and cost centers are preserved."),
    DbOperation("full_reset",     "Full Reset",
                icon="☢️", requires_confirm=True,  destructive=True,
                description="Wipe the entire database. Requires typing the cost center name to confirm."),
]

# ── end of database management config ────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  DEFAULT REGISTRY  —  module-level singleton
# ──────────────────────────────────────────────────────────────────────────────

def build_default_registry() -> FeatureRegistry:
    """
    Returns a FeatureRegistry with all known features registered.
    Enabled = implemented + tested.  Disabled = on the roadmap.
    """
    reg = FeatureRegistry()

    # ── Live features ─────────────────────────────────────────────────────────
    reg.add("inventory",            "Inventory Items View",          True)
    reg.add("history",              "Change History",                True)
    reg.add("gl_codes",             "GL Code Manager",               True)
    reg.add("vendor_import",        "Vendor Invoice Import",         True)
    reg.add("count_import",         "Inventory Count Import",        True)
    reg.add("compare_counts",       "Compare Count Files",           True)
    reg.add("export",               "Export Inventory",              True)
    reg.add("settings",             "Settings & Feature Toggles",    True)
    reg.add("dashboard",            "Dashboard",                     True)
    reg.add("import_mode_selector", "Import Mode Selector",          True)
    reg.add("db_management",        "Database Management",           True)

    # ── Roadmap features — visible but greyed out ─────────────────────────────
    reg.add("pca_engine",           "PCA Creator & Build Sandbox",   False)
    reg.add("transfer_engine",      "Transfer Sheet Generator",      False)
    reg.add("pca_waste_calc",       "Advanced Waste Tracking",       False)
    reg.add("mode_historical",      "Chronological Data Injection",  False)
    reg.add("pca_recursive",        "In-House Product Promotion",    False)

    return reg

# ── end of default registry ───────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  MODULE-LEVEL SINGLETONS  —  import these directly where needed
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_REGISTRY  = build_default_registry()
DEFAULT_MODE_REG  = ModeRegistry()

# ── end of module-level singletons ───────────────────────────────────────────
