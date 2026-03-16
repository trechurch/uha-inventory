"""
UHA Inventory Management System — Streamlit Web App
Streamlit Community Cloud deployment

v4.2.0 — Top nav injected via st.components.v1.html + window.parent so it
          renders as real HTML/CSS at the viewport level, not as escaped text.
          Design tokens, toggle, and menu structure all per spec.
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import io
from datetime import datetime
from pathlib import Path

# ── Page config (must be first Streamlit call) ──────────────────────────────
st.set_page_config(
    page_title="UHA Inventory",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Module imports ───────────────────────────────────────────────────────────
from database import InventoryDatabase
from importer import InventoryImporter
from gl_manager import GLCodeManager
import onedrive_connector as od

__version__ = "4.2.0"

# ────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    return InventoryDatabase()

def get_importer():
    return InventoryImporter(get_db())

def get_gl():
    return GLCodeManager(get_db())

def _init_session():
    defaults = {
        "current_page": "🏠 Dashboard",
        "show_top_nav": True,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# ── end of session state ─────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# QUERY-PARAM ROUTING
# ────────────────────────────────────────────────────────────────────────────

_PAGE_MAP = {
    "dashboard": "🏠 Dashboard",
    "inventory": "📦 Inventory",
    "import":    "📥 Import",
    "gl_codes":  "🏷️ GL Codes",
    "history":   "📜 History",
    "export":    "📤 Export",
    "pca":       "🧪 PCA",
    "app_mgmt":  "⚙️ App Management",
}

def _handle_query_params():
    params = st.query_params
    if "toggle_nav" in params:
        st.session_state.show_top_nav = not st.session_state.show_top_nav
        st.query_params.clear()
        st.rerun()
    if "page" in params:
        key = params["page"]
        if key in _PAGE_MAP:
            st.session_state.current_page = _PAGE_MAP[key]
        st.query_params.clear()
        st.rerun()

# ── end of query-param routing ───────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# TOP NAV — injected via st.components.v1.html → window.parent
# ────────────────────────────────────────────────────────────────────────────

# CSS injected into window.parent.document.head each render
_NAV_CSS = """
:root {
  --color-bg-primary:    #FFFFFF;
  --color-bg-secondary:  #F5F6F8;
  --color-bg-tertiary:   #E9EBEF;
  --color-surface:       #FFFFFF;
  --color-border:        #D0D3D9;
  --color-border-strong: #A8ACB3;
  --color-text-primary:  #1A1C1F;
  --color-text-secondary:#4A4E55;
  --color-text-tertiary: #6E737A;
  --color-accent:        #0066CC;
  --color-accent-hover:  #0052A3;
  --color-accent-muted:  #CCE0F5;
  --font-base:           "Inter","Segoe UI",system-ui,sans-serif;
  --font-xs:             11px;
  --font-sm:             13px;
  --nav-h:               38px;
}
header[data-testid="stHeader"] { display:none !important; }
.main .block-container { padding-top: calc(var(--nav-h) + 18px) !important; }
section[data-testid="stSidebar"] > div:first-child { padding-top: var(--nav-h) !important; }

#uha-topnav {
  position:fixed; top:0; left:0; right:0;
  height:var(--nav-h);
  background:var(--color-bg-primary);
  border-bottom:1px solid var(--color-border);
  display:flex; align-items:center;
  z-index:999999;
  font-family:var(--font-base);
  font-size:var(--font-sm);
  user-select:none;
  box-shadow:0 1px 3px rgba(0,0,0,.06);
}
#uha-topnav .uha-brand {
  display:flex; align-items:center; gap:5px;
  padding:0 12px 0 14px; height:100%;
  border-right:1px solid var(--color-border);
  color:var(--color-accent);
  font-weight:700; font-size:var(--font-sm);
  white-space:nowrap; letter-spacing:.03em;
}
#uha-topnav .uha-brand span { color:var(--color-text-secondary); font-weight:400; }
#uha-topnav .uha-menu { position:relative; height:100%; display:flex; align-items:center; }
#uha-topnav .uha-trigger {
  display:flex; align-items:center; gap:3px;
  padding:0 10px; height:100%;
  cursor:pointer; color:var(--color-text-primary);
  font-weight:500; font-size:var(--font-sm); white-space:nowrap;
  border:none; background:none; outline:none; text-decoration:none;
}
#uha-topnav .uha-trigger:hover,
#uha-topnav .uha-menu:hover > .uha-trigger {
  background:var(--color-accent-muted); color:var(--color-accent);
}
#uha-topnav .uha-caret { font-size:8px; opacity:.5; margin-top:1px; }
#uha-topnav .uha-dropdown {
  display:none; position:absolute;
  top:calc(var(--nav-h) - 1px); left:0;
  min-width:196px; background:var(--color-surface);
  border:1px solid var(--color-border); border-top:none;
  border-radius:0 0 6px 6px;
  box-shadow:0 6px 16px rgba(0,0,0,.10);
  z-index:1000000; padding:4px 0;
}
#uha-topnav .uha-menu:hover > .uha-dropdown { display:block; }
#uha-topnav .uha-dropdown a {
  display:flex; align-items:center; gap:8px;
  padding:5px 14px;
  color:var(--color-text-primary);
  font-size:var(--font-sm); font-weight:400;
  text-decoration:none; white-space:nowrap; cursor:pointer; line-height:1.3;
}
#uha-topnav .uha-dropdown a:hover { background:var(--color-accent-muted); color:var(--color-accent); }
#uha-topnav .uha-hint { margin-left:auto; font-size:var(--font-xs); color:var(--color-text-tertiary); padding-left:20px; }
#uha-topnav .uha-sep { height:1px; background:var(--color-border); margin:4px 10px; }
#uha-topnav .uha-ico { width:16px; text-align:center; font-size:13px; flex-shrink:0; }
#uha-topnav .uha-spacer { flex:1; }
#uha-topnav .uha-ver { padding:0 14px; font-size:var(--font-xs); color:var(--color-text-tertiary); }
"""

_HIDDEN_CSS = """
header[data-testid="stHeader"] { display:none !important; }
.main .block-container { padding-top: 16px !important; }
section[data-testid="stSidebar"] > div:first-child { padding-top: 0 !important; }
"""


def _item(ico: str, label: str, href: str = "#", hint: str = "") -> str:
    h = f'<span class="uha-hint">{hint}</span>' if hint else ""
    return f'<a href="{href}"><span class="uha-ico">{ico}</span>{label}{h}</a>'

def _sep() -> str:
    return '<div class="uha-sep"></div>'

def _menu(trigger: str, *rows: str) -> str:
    inner = "".join(rows)
    return (
        f'<div class="uha-menu">'
        f'<div class="uha-trigger">{trigger} <span class="uha-caret">&#9662;</span></div>'
        f'<div class="uha-dropdown">{inner}</div>'
        f'</div>'
    )


def _build_nav_html(ver: str) -> str:
    file_m = _menu("File",
        _item("&#x1F5CB;", "New Tab",    "javascript:window.open(window.location.href,'_blank');", "T"),
        _item("&#x2B1C;",  "New Window", "javascript:window.open(window.location.href,'_blank','width=1400,height=900');", "W"),
        _sep(),
        _item("&#x1F5A8;", "Print",  "javascript:window.print();", "P"),
        _item("&#x2197;",  "Share",  "javascript:(function(){if(navigator.share){navigator.share({title:'UHA Inventory',url:window.location.href})}else{navigator.clipboard.writeText(window.location.href);alert('Link copied')}})();", "S"),
        _item("&#x1F4E4;", "Export", "?page=export", "E"),
        _sep(),
        _item("&#x2715;",  "Close Tab",   "javascript:window.close();", "C"),
        _item("&#x23FB;",  "Exit Window", "javascript:if(confirm('Close UHA Inventory?'))window.close();", "X"),
    )
    dash_m = _menu("Dashboards",
        _item("&#x1F3E0;", "Database Dashboard",  "?page=dashboard", "D"),
        _item("&#x1F4E6;", "Inventory Dashboard", "?page=inventory", "I"),
        _item("&#x1F9EA;", "PCA Dashboard",       "?page=pca",       "P"),
        _item("&#x1F4E5;", "Import Dashboard",    "?page=import",    "M"),
        _item("&#x2699;",  "App Management",      "?page=app_mgmt",  "A"),
    )
    view_m = _menu("View",
        _item("&#x1F50D;", "Zoom",        "javascript:void(0);", "Z"),
        _item("&#x26F6;",  "Full Screen", "javascript:document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen();", "F"),
        _item("&#x1F3A8;", "Style",       "javascript:void(0);", "S"),
        _sep(),
        _item("&#x2630;",  "Toggle Top Nav", "?toggle_nav=1"),
    )
    help_m = _menu("Help",
        _item("&#x2139;",  "About",       "javascript:void(0);", "B"),
        _item("&#x1F195;", "What's New",  "javascript:void(0);", "N"),
        _item("&#x2753;",  "Help Center", "javascript:void(0);", "H"),
        _item("&#x26A0;",  "Report Issue","javascript:void(0);", "I"),
    )
    return (
        f'<div id="uha-topnav">'
        f'<div class="uha-brand">&#x1F3DF;&#xFE0F;&nbsp;<span>UHA IMS</span></div>'
        f'{file_m}{dash_m}{view_m}{help_m}'
        f'<div class="uha-spacer"></div>'
        f'<div class="uha-ver">v{ver}</div>'
        f'</div>'
    )


def render_top_nav():
    """
    Inject the fixed top nav into window.parent (the actual Streamlit page).
    st.components.v1.html runs in an iframe; we reach out via window.parent
    to write the nav bar and its CSS into the real document.
    height=0 keeps the iframe invisible.
    """
    show = st.session_state.get("show_top_nav", True)

    if show:
        css_block = _NAV_CSS
        nav_html  = _build_nav_html(__version__)
    else:
        css_block = _HIDDEN_CSS
        nav_html  = ""

    # Escape backticks so the template literals don't break
    css_safe = css_block.replace("\\", "\\\\").replace("`", "\\`")
    nav_safe  = nav_html.replace("\\", "\\\\").replace("`", "\\`")

    script = f"""
<script>
(function() {{
  var doc = window.parent.document;

  // Remove old nav + style
  ['uha-topnav','uha-nav-style'].forEach(function(id) {{
    var el = doc.getElementById(id);
    if (el) el.remove();
  }});

  // Inject CSS
  var style = doc.createElement('style');
  style.id = 'uha-nav-style';
  style.textContent = `{css_safe}`;
  doc.head.appendChild(style);

  // Inject nav HTML (only when visible)
  var navHtml = `{nav_safe}`;
  if (navHtml.trim() !== '') {{
    var tmp = doc.createElement('div');
    tmp.innerHTML = navHtml;
    var nav = tmp.firstElementChild;
    if (nav) doc.body.insertBefore(nav, doc.body.firstChild);
  }}
}})();
</script>
"""
    components.html(script, height=0, scrolling=False)

# ── end of render_top_nav ────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# ONEDRIVE AUTH SIDEBAR (stub — IT approval pending)
# ────────────────────────────────────────────────────────────────────────────

def onedrive_auth_sidebar():
    with st.sidebar:
        st.markdown("---")
        st.caption("☁️ OneDrive integration pending IT approval")

# ── end of onedrive auth ─────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — DASHBOARD
# ────────────────────────────────────────────────────────────────────────────

def page_dashboard():
    st.title("🏟️ UHA Inventory — Dashboard")
    db = get_db()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Items",  db.count_items("active"))
    c2.metric("Total Value",  f"${db.get_inventory_value():,.2f}")
    c3.metric("Low Stock",    len(db.get_low_stock_items()))
    c4.metric("Last Updated", datetime.now().strftime("%m/%d/%Y"))

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🔴 Low Stock Items")
        low = db.get_low_stock_items()
        if low:
            df = pd.DataFrame(low)[["description", "pack_type",
                                     "quantity_on_hand", "reorder_point", "vendor"]]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.success("All items are stocked above reorder points.")

    with col2:
        st.subheader("📋 Recently Updated")
        items = db.get_all_items()
        if items:
            df = pd.DataFrame(items)
            if "last_updated" in df.columns:
                df = df.sort_values("last_updated", ascending=False).head(15)
            cols = [c for c in ["description", "pack_type", "cost", "vendor",
                                 "last_updated", "status_tag"] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)

# ── end of page_dashboard ────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — INVENTORY
# ────────────────────────────────────────────────────────────────────────────

def page_inventory():
    st.title("📦 Inventory Items")
    db = get_db()

    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        search    = st.text_input("🔍 Search", placeholder="item name, vendor, GL code…")
    with col2:
        gl_filter = st.text_input("GL Code filter", placeholder="411039")
    with col3:
        show_disc = st.checkbox("Show discontinued")

    items = db.search_items(search) if search else db.get_all_items("active" if not show_disc else None)
    if gl_filter:
        items = [i for i in items if (i.get("gl_code") or "").startswith(gl_filter)]

    if not items:
        st.info("No items found.")
        return

    df           = pd.DataFrame(items)
    display_cols = [c for c in [
        "description", "pack_type", "cost", "per", "vendor",
        "gl_code", "gl_name", "status_tag", "quantity_on_hand", "is_chargeable",
    ] if c in df.columns]

    st.caption(f"{len(df)} items")
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("✏️ Edit Item")
    keys         = [i["key"] for i in items]
    selected_key = st.selectbox("Select item to edit", keys,
                                format_func=lambda k: k.split("||")[0])
    if selected_key:
        item = db.get_item(selected_key)
        if item:
            _edit_item_form(db, item)

# ── end of page_inventory ────────────────────────────────────────────────────


def _edit_item_form(db, item: dict):
    with st.form("edit_item"):
        col1, col2, col3 = st.columns(3)

        with col1:
            desc      = st.text_input("Description", value=item.get("description", ""))
            pack_type = st.text_input("Pack Type",   value=item.get("pack_type", ""))
            cost      = st.number_input("Cost", value=float(item.get("cost") or 0), format="%.4f")
            per       = st.text_input("Per",         value=item.get("per", "") or "")

        with col2:
            vendor      = st.text_input("Vendor",      value=item.get("vendor", "") or "")
            item_number = st.text_input("Item #",      value=item.get("item_number", "") or "")
            gl_code     = st.text_input("GL Code",     value=item.get("gl_code", "") or "")
            gl_name     = st.text_input("GL Name",     value=item.get("gl_name", "") or "")

        with col3:
            yield_val  = st.number_input("Yield",       value=float(item.get("yield") or 1.0), format="%.4f")
            conv_ratio = st.number_input("Conv. Ratio", value=float(item.get("conv_ratio") or 1.0), format="%.4f")
            qoh        = st.number_input("Qty on Hand", value=float(item.get("quantity_on_hand") or 0), format="%.2f")
            notes      = st.text_area("Notes",          value=item.get("user_notes", "") or "")

        st.markdown("**Override Locks** — checked = this field won't be changed by imports")
        oc1, oc2, oc3 = st.columns(3)
        lock_pack  = oc1.checkbox("Lock Pack Type",   value=bool(item.get("override_pack_type")))
        lock_yield = oc2.checkbox("Lock Yield",       value=bool(item.get("override_yield")))
        lock_conv  = oc3.checkbox("Lock Conv. Ratio", value=bool(item.get("override_conv_ratio")))

        submitted = st.form_submit_button("💾 Save Changes")

    if submitted:
        updates = {
            "description":      desc.strip().upper(),
            "pack_type":        pack_type.strip().upper(),
            "cost":             cost,
            "per":              per,
            "vendor":           vendor,
            "item_number":      item_number,
            "gl_code":          gl_code,
            "gl_name":          gl_name,
            "yield":            yield_val,
            "conv_ratio":       conv_ratio,
            "quantity_on_hand": qoh,
            "user_notes":       notes,
            "last_updated":     datetime.utcnow(),
        }
        if lock_pack:
            updates["override_pack_type"] = pack_type.strip().upper()
        elif not lock_pack and item.get("override_pack_type"):
            updates["override_pack_type"] = None
        if lock_yield:
            updates["override_yield"] = yield_val
        elif not lock_yield and item.get("override_yield"):
            updates["override_yield"] = None
        if lock_conv:
            updates["override_conv_ratio"] = conv_ratio
        elif not lock_conv and item.get("override_conv_ratio"):
            updates["override_conv_ratio"] = None

        db._apply_update(item["key"], updates, change_source="manual_edit", changed_by="user")
        st.success("✅ Saved!")
        st.rerun()

# ── end of _edit_item_form ───────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — IMPORT
# ────────────────────────────────────────────────────────────────────────────

def page_import():
    st.title("📥 Import Files")
    db       = get_db()
    importer = get_importer()

    tab1, tab2 = st.tabs(["📤 Upload from Computer", "☁️ Import from OneDrive"])

    with tab1:
        st.subheader("Upload Invoice or Inventory CSV")
        uploaded = st.file_uploader(
            "Drop vendor invoice CSV or PAC export here",
            type=["csv", "xlsx"],
            accept_multiple_files=True,
        )

        if uploaded:
            for f in uploaded:
                st.markdown(f"**{f.name}**")
                try:
                    content = f.read()
                    import tempfile, os
                    suffix = ".csv" if f.name.endswith(".csv") else ".xlsx"
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name

                    df = importer.read_file(tmp_path)
                    os.unlink(tmp_path)

                    if df is None:
                        st.error(f"Could not read: {importer.errors}")
                        continue

                    analysis = importer.analyze_import(df)
                    col1, col2, col3 = st.columns(3)
                    col1.metric("New Items",   len(analysis["new_items"]))
                    col2.metric("Updates",     len(analysis["updates"]))
                    col3.metric("Skipped/Err", len(analysis["skipped"]) + len(analysis["errors"]))

                    if analysis["new_items"]:
                        with st.expander(f"📋 {len(analysis['new_items'])} New Items"):
                            st.dataframe(
                                pd.DataFrame([{"Key": i["key"], "Description": i["description"]}
                                              for i in analysis["new_items"]]),
                                use_container_width=True, hide_index=True,
                            )

                    if analysis["updates"]:
                        with st.expander(f"🔄 {len(analysis['updates'])} Updates"):
                            st.dataframe(
                                pd.DataFrame([{"Key": i["key"],
                                               "Fields Changed": ", ".join(i["changes"].keys())}
                                              for i in analysis["updates"]]),
                                use_container_width=True, hide_index=True,
                            )

                    if st.button(f"✅ Confirm Import — {f.name}", key=f"confirm_{f.name}"):
                        results = importer.execute_import(
                            analysis,
                            changed_by="web_import",
                            source_document=f.name,
                            doc_date=datetime.now().strftime("%Y-%m-%d"),
                        )
                        st.success(
                            f"Done! {results['new_items_added']} added, "
                            f"{results['items_updated']} updated."
                        )
                        if od.get_access_token():
                            od.archive_file(f.name, content)
                            st.info("📁 Archived to OneDrive.")

                except Exception as e:
                    st.error(f"Error processing {f.name}: {e}")

    with tab2:
        if not od.get_access_token():
            st.warning("Connect to OneDrive first (sidebar).")
        else:
            st.subheader("Files waiting in OneDrive Imports folder")
            files = od.list_import_files()
            if not files:
                st.info("No files found in your OneDrive Imports folder.")
            else:
                for f in files:
                    col1, col2, col3 = st.columns([4, 2, 1])
                    col1.write(f["name"])
                    col2.write(f.get("modified", "")[:10])
                    if col3.button("Import", key=f"od_{f['name']}"):
                        content = od.download_import_file(f["name"])
                        if content:
                            st.info(f"Processing {f['name']}…")
                            import tempfile, os
                            suffix = Path(f["name"]).suffix
                            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                                tmp.write(content)
                                tmp_path = tmp.name
                            analysis, results = importer.import_file(
                                tmp_path, changed_by="onedrive_import"
                            )
                            os.unlink(tmp_path)
                            st.success(
                                f"Done: {results.get('new_items_added',0)} added, "
                                f"{results.get('items_updated',0)} updated."
                            )
                            od.archive_file(f["name"], content)

# ── end of page_import ───────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — GL CODES
# ────────────────────────────────────────────────────────────────────────────

def page_gl_codes():
    st.title("🏷️ GL Code Manager")
    db = get_db()
    gl = get_gl()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Load GL Lists from OneDrive")
        if not od.get_access_token():
            st.warning("Connect OneDrive to load GL lists automatically.")
        else:
            if st.button("🔄 Reload GL Lists from OneDrive"):
                with st.spinner("Loading…"):
                    entries = od.load_gl_files_from_onedrive()
                    for gl_code, gl_name, desc in entries:
                        gl.add_gl_mapping(gl_code, gl_name, desc)
                st.success(f"Loaded {len(entries):,} GL entries.")

        st.subheader("Auto-Assign GL Codes")
        confidence = st.slider("Minimum confidence", 0.5, 0.95, 0.70)
        if st.button("🤖 Auto-Assign to Unassigned Items"):
            with st.spinner("Matching…"):
                results = gl.assign_gl_codes_to_items(min_confidence=confidence)
            st.success(
                f"Assigned: {results['assigned']} | "
                f"Skipped: {results['skipped']} | "
                f"Failed: {results['failed']}"
            )
            if results["assignments"]:
                adf = pd.DataFrame(results["assignments"])[
                    ["description", "gl_code", "gl_name", "confidence"]
                ]
                st.dataframe(adf, use_container_width=True, hide_index=True)

    with col2:
        st.subheader("GL Code Summary")
        summary = gl.get_gl_summary()
        if summary:
            st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)
        else:
            st.info("No GL mappings loaded yet.")

# ── end of page_gl_codes ─────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — HISTORY
# ────────────────────────────────────────────────────────────────────────────

def page_history():
    st.title("📜 Change History")
    db        = get_db()
    key_input = st.text_input("Enter item key or search term")

    if key_input:
        history = db.get_item_history(key_input)
        if not history:
            items = db.search_items(key_input)
            if items:
                keys     = [i["key"] for i in items]
                selected = st.selectbox("Select item", keys,
                                        format_func=lambda k: k.split("||")[0])
                history  = db.get_item_history(selected)

        if history:
            df   = pd.DataFrame(history)
            cols = [c for c in ["change_date", "change_type", "field_changed",
                                 "old_value", "new_value", "change_source",
                                 "changed_by", "source_document"] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
        else:
            st.info("No history found.")

# ── end of page_history ──────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — EXPORT
# ────────────────────────────────────────────────────────────────────────────

def page_export():
    st.title("📤 Export")
    db    = get_db()
    items = db.get_all_items()

    st.subheader("Export Full Inventory")
    if items:
        df     = pd.DataFrame(items)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Inventory")
        buffer.seek(0)
        st.download_button(
            "⬇️ Download as Excel", data=buffer,
            file_name=f"inventory_export_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.download_button(
            "⬇️ Download as CSV", data=df.to_csv(index=False),
            file_name=f"inventory_export_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )
        st.caption(f"{len(df)} items")

    if od.get_access_token():
        st.subheader("Save to OneDrive")
        if st.button("☁️ Export to OneDrive"):
            buffer = io.BytesIO()
            df.to_excel(buffer, index=False)
            filename = f"inventory_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            od.archive_file(filename, buffer.getvalue(), subfolder="Exports")
            st.success(f"Saved {filename} to OneDrive Archives.")

# ── end of page_export ───────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — PCA (stub)
# ────────────────────────────────────────────────────────────────────────────

def page_pca():
    st.title("🧪 PCA Dashboard")
    st.info("PCA engine is under construction. Check back soon.")

# ── end of page_pca ──────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# PAGE — APP MANAGEMENT (stub)
# ────────────────────────────────────────────────────────────────────────────

def page_app_mgmt():
    st.title("⚙️ App Management")
    st.info("App Management dashboard is under construction.")

# ── end of page_app_mgmt ─────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────────

_PAGES = {
    "🏠 Dashboard":      page_dashboard,
    "📦 Inventory":      page_inventory,
    "📥 Import":         page_import,
    "🏷️ GL Codes":       page_gl_codes,
    "📜 History":        page_history,
    "📤 Export":         page_export,
    "🧪 PCA":            page_pca,
    "⚙️ App Management": page_app_mgmt,
}

def main():
    # 1. Session defaults
    _init_session()

    # 2. URL param routing (toggle_nav / page)
    _handle_query_params()

    # 3. Inject top nav into window.parent — must come before sidebar/content
    render_top_nav()

    # 4. Sidebar
    with st.sidebar:
        st.image("https://img.icons8.com/emoji/96/stadium.png", width=48)
        st.markdown("**UHA Inventory**")
        st.caption("TDECU Stadium — Compass Group")
        st.markdown("---")

        # Top nav visibility toggle
        show_nav = st.checkbox(
            "☰  Top Navigation Bar",
            value=st.session_state.show_top_nav,
        )
        if show_nav != st.session_state.show_top_nav:
            st.session_state.show_top_nav = show_nav
            st.rerun()

        st.markdown("---")

        # Page navigation
        page_label = st.radio(
            "Navigate",
            list(_PAGES.keys()),
            index=list(_PAGES.keys()).index(
                st.session_state.get("current_page", "🏠 Dashboard")
            ),
        )
        st.session_state.current_page = page_label

    onedrive_auth_sidebar()

    # 5. Render active page
    _PAGES[st.session_state.current_page]()


if __name__ == "__main__":
    main()
