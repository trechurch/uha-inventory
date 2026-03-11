# ──────────────────────────────────────────────────────────────────────────────
#  ui_skeleton.py  —  Feature Registry, Menu Definitions, Sidebar Config
#  Ported from Tkinter prototype + extended for Streamlit deployment
# ──────────────────────────────────────────────────────────────────────────────

from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional

# ── end of imports ────────────────────────────────────────────────────────────


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
    visible:        bool = True
    show_nav:       bool = True    # show page quick-links
    show_cost_center: bool = True  # show active cost center badge
    show_recent:    bool = True    # show recent imports list
    custom_label:   str  = "UHA TDECU Stadium"

# ── end of sidebar config ─────────────────────────────────────────────────────


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
    reg.add("inventory",       "Inventory Items View",         True)
    reg.add("history",         "Change History",               True)
    reg.add("gl_codes",        "GL Code Manager",              True)
    reg.add("vendor_import",   "Vendor Invoice Import",        True)
    reg.add("count_import",    "Inventory Count Import",       True)
    reg.add("compare_counts",  "Compare Count Files",          True)
    reg.add("export",          "Export Inventory",             True)
    reg.add("settings",        "Settings & Feature Toggles",   True)
    reg.add("dashboard",       "Dashboard",                    True)

    # ── Roadmap features — visible but greyed out ─────────────────────────────
    reg.add("pca_engine",      "PCA Creator & Build Sandbox",  False)
    reg.add("transfer_engine", "Transfer Sheet Generator",     False)
    reg.add("pca_waste_calc",  "Advanced Waste Tracking",      False)
    reg.add("mode_historical", "Chronological Data Injection", False)
    reg.add("pca_recursive",   "In-House Product Promotion",   False)

    return reg

# ── end of default registry ───────────────────────────────────────────────────
