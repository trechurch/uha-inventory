"""
Inventory Management System - Streamlit Web App
Runs from anywhere via Streamlit Community Cloud
v1.2 — + Portion Cost Analysis (PCA) page
"""

import streamlit as st
import pandas as pd
import io
import json
from datetime import datetime
from typing import Optional

# ── Page config (must be first Streamlit call) ──────────────────────
st.set_page_config(
    page_title="UHA Inventory",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Module imports ───────────────────────────────────────────────────
from database import InventoryDatabase
from importer import InventoryImporter
from gl_manager import GLCodeManager
from pca_engine import PCAEngine, INGREDIENT_TYPE_FOOD, INGREDIENT_TYPE_DISPOSABLE
import onedrive_connector as od


# ────────────────────────────────────────────────────────────────────
# SESSION STATE HELPERS
# ────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_db():
    return InventoryDatabase()


def get_importer():
    return InventoryImporter(get_db())


def get_gl():
    return GLCodeManager(get_db())


@st.cache_resource
def get_pca():
    return PCAEngine(get_db())


# ────────────────────────────────────────────────────────────────────
# ONEDRIVE AUTH SIDEBAR
# ────────────────────────────────────────────────────────────────────
def onedrive_auth_sidebar():
    with st.sidebar:
        st.markdown("---")
        st.caption("☁️ OneDrive integration pending IT approval")


# ────────────────────────────────────────────────────────────────────
# PAGES — EXISTING
# ────────────────────────────────────────────────────────────────────

def page_dashboard():
    st.title("🏟️ UHA Inventory — Dashboard")
    db = get_db()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Items",    db.count_items("active"))
    c2.metric("Total Value",    f"${db.get_inventory_value():,.2f}")
    c3.metric("Low Stock",      len(db.get_low_stock_items()))
    c4.metric("Last Updated",   datetime.now().strftime("%m/%d/%Y"))

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🔴 Low Stock Items")
        low = db.get_low_stock_items()
        if low:
            df = pd.DataFrame(low)[["description", "pack_type", "quantity_on_hand", "reorder_point", "vendor"]]
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
            cols = [c for c in ["description", "pack_type", "cost", "vendor", "last_updated", "status_tag"] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)


def page_inventory():
    st.title("📦 Inventory Items")
    db = get_db()

    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        search = st.text_input("🔍 Search", placeholder="item name, vendor, GL code...")
    with col2:
        gl_filter = st.text_input("GL Code filter", placeholder="411039")
    with col3:
        show_disc = st.checkbox("Show discontinued")

    if search:
        items = db.search_items(search)
    else:
        items = db.get_all_items("active" if not show_disc else None)

    if gl_filter:
        items = [i for i in items if (i.get("gl_code") or "").startswith(gl_filter)]

    if not items:
        st.info("No items found.")
        return

    df = pd.DataFrame(items)
    display_cols = [c for c in [
        "description", "pack_type", "cost", "per", "vendor",
        "gl_code", "gl_name", "status_tag", "quantity_on_hand",
        "is_chargeable", "cost_center"
    ] if c in df.columns]

    st.caption(f"{len(df)} items")
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("✏️ Edit Item")

    keys = [i["key"] for i in items]
    selected_key = st.selectbox("Select item to edit", keys,
                                format_func=lambda k: k.split("||")[0])

    if selected_key:
        item = db.get_item(selected_key)
        if item:
            _edit_item_form(db, item)


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
            "description": desc.strip().upper(),
            "pack_type":   pack_type.strip().upper(),
            "cost":        cost,
            "per":         per,
            "vendor":      vendor,
            "item_number": item_number,
            "gl_code":     gl_code,
            "gl_name":     gl_name,
            "yield":       yield_val,
            "conv_ratio":  conv_ratio,
            "quantity_on_hand": qoh,
            "user_notes":  notes,
            "last_updated": datetime.utcnow(),
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

        db._apply_update(item["key"], updates,
                         change_source="manual_edit", changed_by="user")
        st.success("✅ Saved!")
        st.rerun()


def page_import():
    st.title("📥 Import Files")
    db = get_db()
    importer = get_importer()

    tab1, tab2 = st.tabs(["📤 Upload from Computer", "☁️ Import from OneDrive"])

    with tab1:
        st.subheader("Upload Invoice or Inventory CSV")
        uploaded = st.file_uploader(
            "Drop vendor invoice CSV or PAC export here",
            type=["csv", "xlsx"],
            accept_multiple_files=True
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
                    col1.metric("New Items",    len(analysis["new_items"]))
                    col2.metric("Updates",      len(analysis["updates"]))
                    col3.metric("Skipped/Err",  len(analysis["skipped"]) + len(analysis["errors"]))

                    if analysis["new_items"]:
                        with st.expander(f"📋 {len(analysis['new_items'])} New Items"):
                            new_df = pd.DataFrame([
                                {"Key": i["key"], "Description": i["description"]}
                                for i in analysis["new_items"]
                            ])
                            st.dataframe(new_df, use_container_width=True, hide_index=True)

                    if analysis["updates"]:
                        with st.expander(f"🔄 {len(analysis['updates'])} Updates"):
                            upd_df = pd.DataFrame([
                                {
                                    "Key": i["key"],
                                    "Fields Changed": ", ".join(i["changes"].keys())
                                }
                                for i in analysis["updates"]
                            ])
                            st.dataframe(upd_df, use_container_width=True, hide_index=True)

                    if st.button(f"✅ Confirm Import — {f.name}", key=f"confirm_{f.name}"):
                        results = importer.execute_import(
                            analysis,
                            changed_by="web_import",
                            source_document=f.name,
                            doc_date=datetime.now().strftime("%Y-%m-%d")
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
                            st.info(f"Processing {f['name']}...")
                            import tempfile, os
                            from pathlib import Path
                            suffix = Path(f["name"]).suffix
                            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                                tmp.write(content)
                                tmp_path = tmp.name
                            analysis, results = importer.import_file(
                                tmp_path, changed_by="onedrive_import"
                            )
                            os.unlink(tmp_path)
                            st.success(
                                f"Done: {results.get('new_items_added', 0)} added, "
                                f"{results.get('items_updated', 0)} updated."
                            )
                            od.archive_file(f["name"], content)


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
                with st.spinner("Loading..."):
                    entries = od.load_gl_files_from_onedrive()
                    for gl_code, gl_name, desc in entries:
                        gl.add_gl_mapping(gl_code, gl_name, desc)
                st.success(f"Loaded {len(entries):,} GL entries.")

        st.subheader("Auto-Assign GL Codes")
        confidence = st.slider("Minimum confidence", 0.5, 0.95, 0.70)
        if st.button("🤖 Auto-Assign to Unassigned Items"):
            with st.spinner("Matching..."):
                results = gl.assign_gl_codes_to_items(min_confidence=confidence)
            st.success(
                f"Assigned: {results['assigned']} | "
                f"Skipped: {results['skipped']} | Failed: {results['failed']}"
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
            sdf = pd.DataFrame(summary)
            st.dataframe(sdf, use_container_width=True, hide_index=True)
        else:
            st.info("No GL mappings loaded yet.")


def page_history():
    st.title("📜 Change History")
    db = get_db()

    key_input = st.text_input("Enter item key or search term")
    if key_input:
        history = db.get_item_history(key_input)
        if not history:
            items = db.search_items(key_input)
            if items:
                keys = [i["key"] for i in items]
                selected = st.selectbox("Select item", keys,
                                        format_func=lambda k: k.split("||")[0])
                history = db.get_item_history(selected)

        if history:
            df = pd.DataFrame(history)
            cols = [c for c in ["change_date", "change_type", "field_changed",
                                 "old_value", "new_value", "change_source",
                                 "changed_by", "source_document"] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
        else:
            st.info("No history found.")


def page_export():
    st.title("📤 Export")
    db = get_db()

    st.subheader("Export Full Inventory")
    items = db.get_all_items()
    if items:
        df = pd.DataFrame(items)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Inventory")
        buffer.seek(0)
        st.download_button(
            "⬇️ Download as Excel",
            data=buffer,
            file_name=f"inventory_export_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        csv = df.to_csv(index=False)
        st.download_button(
            "⬇️ Download as CSV",
            data=csv,
            file_name=f"inventory_export_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
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


# ────────────────────────────────────────────────────────────────────
# PAGE — PORTION COST ANALYSIS
# ────────────────────────────────────────────────────────────────────

def page_pca():
    st.title("🍽️ Portion Cost Analysis")
    pca_engine = get_pca()
    db         = get_db()

    # ── Initialise session state ─────────────────────────────────────
    if "pca_recipe_id"   not in st.session_state:
        st.session_state["pca_recipe_id"]   = None
    if "pca_show_create" not in st.session_state:
        st.session_state["pca_show_create"] = False

    # ── Two-column layout: recipe list left, detail right ────────────
    recipes = pca_engine.get_all_recipes()

    col_left, col_right = st.columns([1, 3])

    with col_left:
        st.subheader("Recipes")
        if st.button("➕ New Recipe", use_container_width=True):
            st.session_state["pca_show_create"] = True
            st.session_state["pca_recipe_id"]   = None

        if recipes:
            recipe_options = {r["recipe_id"]: r["name"] for r in recipes}
            selected_id = st.radio(
                "Select recipe",
                options=list(recipe_options.keys()),
                format_func=lambda rid: recipe_options[rid],
                index=0,
                label_visibility="collapsed",
            )
            if selected_id != st.session_state["pca_recipe_id"]:
                st.session_state["pca_recipe_id"]   = selected_id
                st.session_state["pca_show_create"] = False
        else:
            st.info("No recipes yet.")
            selected_id = None

    with col_right:

        # ── Create new recipe form ───────────────────────────────────
        if st.session_state["pca_show_create"]:
            _pca_create_recipe_form(pca_engine)
            return

        recipe_id = st.session_state.get("pca_recipe_id")
        if not recipe_id:
            st.info("Select a recipe or create a new one.")
            return

        recipe = pca_engine.get_recipe(recipe_id)
        if not recipe:
            st.error("Recipe not found.")
            return

        # ── Tab layout ───────────────────────────────────────────────
        tab_cost, tab_ingredients, tab_disposables, tab_ai, tab_card, tab_settings = st.tabs([
            "📊 Cost Summary",
            "🥩 Food Ingredients",
            "📦 Disposables",
            "🤖 AI Suggestions",
            "📄 Recipe Card",
            "⚙️ Settings",
        ])

        # ── TAB: COST SUMMARY ────────────────────────────────────────
        with tab_cost:
            pca = pca_engine.calculate_pca(recipe_id)
            if not pca:
                st.warning("Could not calculate PCA.")
                st.stop()

            metrics = pca["metrics"]
            totals  = pca["totals"]

            h1, h2, h3, h4 = st.columns(4)
            h1.markdown(f"**Menu Item**\n\n{recipe.get('name', '—')}")
            h2.markdown(f"**Category**\n\n{recipe.get('category', '—')}")
            h3.markdown(f"**Updated By**\n\n{recipe.get('updated_by', '—')}")
            h4.markdown(f"**Date**\n\n{str(recipe.get('recipe_date',''))[:10] or '—'}")
            st.markdown("---")

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Selling Price",    f"${metrics['selling_price']:.2f}")
            m2.metric("Cost Per Portion", f"${totals['cost_per_portion']:.4f}")
            m3.metric("Cost Per Serving", f"${totals['cost_per_serving']:.4f}")

            actual_pct  = metrics["product_cost_pct"] * 100
            goal_pct    = metrics["cost_pct_goal"] * 100
            delta_pct   = metrics["pct_diff"] * 100
            m4.metric(
                "Product Cost %",
                f"{actual_pct:.2f}%",
                delta=f"{delta_pct:+.2f}% vs {goal_pct:.0f}% goal",
                delta_color="inverse",
            )
            m5.metric(
                "Per Serving Cost Goal",
                f"${metrics['per_serving_cost_goal']:.4f}",
                help=f"Selling price × {goal_pct:.0f}% cost goal",
            )

            if metrics["over_goal"]:
                st.error(
                    f"⚠️ Cost % is **{actual_pct:.2f}%** — "
                    f"**{abs(delta_pct):.2f}% over** the {goal_pct:.0f}% goal. "
                    "Check the 🤖 AI Suggestions tab."
                )
            else:
                st.success(
                    f"✅ Cost % is **{actual_pct:.2f}%** — "
                    f"**{abs(delta_pct):.2f}% under** the {goal_pct:.0f}% goal."
                )

            st.markdown("---")

            # Food table
            st.subheader("🥩 Food Ingredient Costs")
            if pca["food_lines"]:
                food_rows = [{
                    "Ingredient":     l.get("description") or l.get("item_key", ""),
                    "EP Amount":      l["ep_amount"],
                    "Unit":           l["unit"],
                    "Invoice Amount": f"${float(l['invoice_amount']):.4f}",
                    "Conv. Ratio":    l["conv_ratio"],
                    "Yield %":        f"{float(l['yield_pct'])*100:.1f}%",
                    "ES Amount":      l["es_amount"],
                    "ES Cost":        f"${l['es_cost']:.4f}",
                    "EP Cost":        f"${l['ep_cost']:.4f}",
                    "Vendor":         l.get("vendor", ""),
                } for l in pca["food_lines"]]
                st.dataframe(pd.DataFrame(food_rows), use_container_width=True, hide_index=True)
                st.metric("Total Food Cost", f"${totals['total_food_cost']:.4f}")
            else:
                st.info("No food ingredients added yet.")

            # Disposables table
            if pca["disposable_lines"]:
                st.subheader("📦 Disposable Costs")
                disp_rows = [{
                    "Item":             l.get("description") or l.get("item_key", ""),
                    "Units Per Serving": l["ep_amount"],
                    "Unit":             l["unit"],
                    "Invoice Amount":   f"${float(l['invoice_amount']):.4f}",
                    "Conv. Ratio":      l["conv_ratio"],
                    "Yield %":          f"{float(l['yield_pct'])*100:.1f}%",
                    "Cost Per Portion": f"${l['cost_per_portion']:.4f}",
                    "Cost Per Serving": f"${l['cost_per_serving']:.4f}",
                } for l in pca["disposable_lines"]]
                st.dataframe(pd.DataFrame(disp_rows), use_container_width=True, hide_index=True)
                st.metric("Total Disposable Cost", f"${totals['total_disposable_cost']:.4f}")

        # ── TAB: FOOD INGREDIENTS ────────────────────────────────────
        with tab_ingredients:
            _pca_ingredients_tab(pca_engine, db, recipe_id, INGREDIENT_TYPE_FOOD)

        # ── TAB: DISPOSABLES ─────────────────────────────────────────
        with tab_disposables:
            _pca_ingredients_tab(pca_engine, db, recipe_id, INGREDIENT_TYPE_DISPOSABLE)

        # ── TAB: AI SUGGESTIONS ──────────────────────────────────────
        with tab_ai:
            st.subheader("🤖 AI-Generated Alternative Ingredient Options")
            st.caption(
                "Scans your active inventory and suggests lower-cost ingredient swaps "
                "that preserve the menu item's intent."
            )

            pca = pca_engine.calculate_pca(recipe_id)
            if pca:
                m = pca["metrics"]
                st.info(
                    f"Current cost: **${pca['totals']['cost_per_portion']:.4f}** per portion "
                    f"({m['product_cost_pct']*100:.2f}%) | "
                    f"Goal: **{m['cost_pct_goal']*100:.0f}%** "
                    f"(≤ ${m['per_serving_cost_goal']:.4f})"
                )

            max_suggestions = st.slider("Max suggestions", 3, 15, 8)

            if st.button("✨ Generate Suggestions", type="primary"):
                with st.spinner("Reviewing inventory and generating suggestions..."):
                    try:
                        suggestions = pca_engine.generate_ai_suggestions(
                            recipe_id, max_suggestions=max_suggestions
                        )
                        st.session_state["pca_ai_suggestions"] = suggestions
                    except Exception as e:
                        st.error(f"Error generating suggestions: {e}")
                        st.session_state["pca_ai_suggestions"] = []

            suggestions = st.session_state.get("pca_ai_suggestions", [])
            if suggestions:
                _pca_render_suggestions(suggestions, pca)
            elif "pca_ai_suggestions" in st.session_state:
                st.info("No alternative suggestions found in current inventory.")

        # ── TAB: RECIPE CARD ─────────────────────────────────────────
        with tab_card:
            _pca_recipe_card(pca_engine, recipe_id)

        # ── TAB: SETTINGS ────────────────────────────────────────────
        with tab_settings:
            _pca_settings_tab(pca_engine, recipe_id, recipe)


# ── PCA sub-functions ────────────────────────────────────────────────

def _pca_create_recipe_form(pca_engine: PCAEngine):
    st.subheader("➕ New Portion Cost Analysis")
    with st.form("create_recipe"):
        col1, col2 = st.columns(2)
        with col1:
            name           = st.text_input("Menu Item Name*", placeholder="House Smoked Brisket Sandwich")
            category       = st.text_input("Category",        value="Concessions")
            component_name = st.text_input("Component / Venue", value="TDECU Stadium")
        with col2:
            selling_price  = st.number_input("Selling Price ($)", value=0.00, format="%.2f")
            cost_pct_goal  = st.number_input(
                "Cost % Goal (e.g. 0.17 = 17%)", value=0.17, format="%.4f",
                min_value=0.01, max_value=1.0,
            )
            updated_by = st.text_input("Updated By", placeholder="Initials")
        col3, col4 = st.columns(2)
        with col3:
            servings = st.number_input("Servings Per Portion", value=1, min_value=1)
        with col4:
            portions = st.number_input("Portions", value=1, min_value=1)

        submitted = st.form_submit_button("Create Recipe", type="primary")

    if submitted:
        if not name.strip():
            st.error("Menu item name is required.")
        else:
            rid = pca_engine.create_recipe(
                name=name.strip(),
                category=category,
                component_name=component_name,
                selling_price=selling_price,
                cost_pct_goal=cost_pct_goal,
                servings_per_portion=int(servings),
                portions=int(portions),
                updated_by=updated_by,
            )
            st.success(f"Recipe created!")
            st.session_state["pca_recipe_id"]   = rid
            st.session_state["pca_show_create"] = False
            st.rerun()


def _pca_ingredients_tab(pca_engine, db, recipe_id: int, ingredient_type: str):
    """Shared ingredient editor for food and disposables tabs."""
    from pca_engine import calc_unit_cost, calc_ep_cost, _safe_float

    is_food  = ingredient_type == INGREDIENT_TYPE_FOOD
    label    = "Food Ingredient" if is_food else "Disposable"
    icon     = "🥩" if is_food else "📦"
    ep_label = "EP Amount" if is_food else "Units Per Serving"

    st.subheader(f"{icon} {label}s")

    lines = [
        l for l in pca_engine.get_recipe_lines(recipe_id)
        if l["ingredient_type"] == ingredient_type
    ]

    # ── Existing lines ───────────────────────────────────────────────
    if lines:
        for line in lines:
            desc = line.get("description") or line.get("item_key") or "Unknown"
            with st.expander(f"{desc}  —  {line['ep_amount']} {line['unit']}", expanded=False):
                ecol1, ecol2, ecol3, ecol4 = st.columns([2, 1, 1, 1])

                new_amount = ecol1.number_input(
                    ep_label, value=float(line["ep_amount"] or 1),
                    format="%.4f", key=f"ep_{line['line_id']}",
                )
                new_unit = ecol2.text_input(
                    "Unit", value=line.get("unit", "Each"),
                    key=f"unit_{line['line_id']}",
                )

                uc          = calc_unit_cost(
                    _safe_float(line["invoice_amount"]),
                    _safe_float(line["conv_ratio"], 1.0),
                    _safe_float(line["yield_pct"], 1.0),
                )
                cost_preview = calc_ep_cost(uc, float(new_amount))
                ecol3.metric("Cost Preview", f"${cost_preview:.4f}")

                save_col, del_col = ecol4.columns(2)
                if save_col.button("💾", key=f"save_{line['line_id']}", help="Save"):
                    pca_engine.update_ingredient(
                        line["line_id"],
                        {"ep_amount": new_amount, "unit": new_unit.strip()},
                    )
                    st.rerun()
                if del_col.button("🗑️", key=f"del_{line['line_id']}", help="Remove"):
                    pca_engine.remove_ingredient(line["line_id"])
                    st.rerun()

                st.caption(
                    f"Invoice: ${float(line['invoice_amount']):.4f} | "
                    f"Conv: {float(line['conv_ratio']):.1f} | "
                    f"Yield: {float(line['yield_pct'])*100:.1f}% | "
                    f"Vendor: {line.get('vendor','—')} | "
                    f"GL: {line.get('gl_code','—')}"
                )
    else:
        st.info(f"No {label.lower()}s added yet.")

    st.markdown("---")
    st.markdown(f"**Add {label}**")

    all_items = db.get_all_items("active") or []
    if not all_items:
        st.warning("No active inventory items. Import invoices first.")
        return

    item_options = {
        i["key"]: i.get("description") or i["key"]
        for i in sorted(all_items, key=lambda x: (x.get("description") or ""))
    }

    add1, add2, add3, add4 = st.columns([3, 1, 1, 1])
    with add1:
        new_key = st.selectbox(
            f"Select {label}", options=list(item_options.keys()),
            format_func=lambda k: item_options.get(k, k),
            key=f"add_item_{ingredient_type}",
        )
    with add2:
        new_ep = st.number_input(
            ep_label, value=1.0, format="%.4f",
            key=f"add_ep_{ingredient_type}",
        )
    with add3:
        default_unit = "Each"
        if new_key:
            matched = next((i for i in all_items if i["key"] == new_key), None)
            if matched:
                default_unit = matched.get("unit") or matched.get("pack_type") or "Each"
        new_unit = st.text_input("Unit", value=default_unit, key=f"add_unit_{ingredient_type}")
    with add4:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕ Add", key=f"btn_add_{ingredient_type}", use_container_width=True):
            if new_key:
                pca_engine.add_ingredient(
                    recipe_id=recipe_id,
                    item_key=new_key,
                    ep_amount=new_ep,
                    unit=new_unit.strip(),
                    ingredient_type=ingredient_type,
                )
                st.rerun()


def _pca_render_suggestions(suggestions: list, pca: dict):
    current_pct = pca["metrics"]["product_cost_pct"] * 100
    goal_pct    = pca["metrics"]["cost_pct_goal"] * 100

    st.caption(f"{len(suggestions)} suggestion(s) — sorted cheapest first.")

    for s in suggestions:
        alt_pct   = s["product_cost_pct_effect"] * 100
        variance  = s["alternate_cost_variance"]
        sign      = "🟢" if variance < 0 else "🔴"
        label     = (
            f"{sign} **Replace** {s['ingredient_to_replace']}  →  "
            f"**{s['alternate_description']}** ({s['alternate_vendor']})"
        )
        with st.expander(label, expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("New Cost/Portion",  f"${s['alternate_cost_per_portion']:.4f}",
                      delta=f"${variance:+.4f}", delta_color="inverse")
            c2.metric("Ingredient Cost",   f"${s['alt_ep_cost']:.4f}",
                      delta=f"${s['alt_ep_cost'] - s['orig_ep_cost']:+.4f}",
                      delta_color="inverse")
            c3.metric("New Product Cost %", f"{alt_pct:.2f}%",
                      delta=f"{alt_pct - current_pct:+.2f}%", delta_color="inverse")
            c4.metric("vs Goal",
                      "Over ⚠️" if alt_pct > goal_pct else "Under ✅",
                      delta=f"{alt_pct - goal_pct:+.2f}%", delta_color="inverse")
            st.caption(f"Reason: {s.get('reason', '')}")


def _pca_recipe_card(pca_engine, recipe_id: int):
    pca    = pca_engine.calculate_pca(recipe_id)
    recipe = pca.get("recipe", {})

    st.subheader("📄 Recipe Card")
    r1, r2 = st.columns(2)
    r1.markdown(
        f"**Recipe Item:** {recipe.get('name','')}\n\n"
        f"**Category:** {recipe.get('category','')}\n\n"
        f"**Recipe Quantity:** {recipe.get('portions', 1)}"
    )
    r2.markdown(
        f"**Component:** {recipe.get('component_name','')}\n\n"
        f"**Date:** {str(recipe.get('recipe_date',''))[:10]}\n\n"
        f"**Batch Cost:** ${pca['totals']['cost_per_portion']:.4f}"
    )

    st.markdown("---")
    st.markdown("**Food Ingredients**")
    if pca["food_lines"]:
        st.dataframe(pd.DataFrame([{
            "Ingredient": l.get("description") or l.get("item_key", ""),
            "Quantity":   l["ep_amount"],
            "Unit":       l["unit"],
        } for l in pca["food_lines"]]), use_container_width=True, hide_index=True)

    if pca["disposable_lines"]:
        st.markdown("**Disposables**")
        st.dataframe(pd.DataFrame([{
            "Item":     l.get("description") or l.get("item_key", ""),
            "Quantity": l["ep_amount"],
            "Unit":     l["unit"],
        } for l in pca["disposable_lines"]]), use_container_width=True, hide_index=True)

    st.markdown("**Preparation Notes**")
    if recipe.get("notes"):
        st.text(recipe["notes"])
    else:
        st.info("No preparation notes — add them in the ⚙️ Settings tab.")

    st.markdown("---")
    pca_json = pca_engine.export_pca_dict(recipe_id)
    st.download_button(
        "⬇️ Export PCA as JSON",
        data=json.dumps(pca_json, indent=2),
        file_name=f"pca_{recipe.get('name','recipe').replace(' ','_')}.json",
        mime="application/json",
    )


def _pca_settings_tab(pca_engine, recipe_id: int, recipe: dict):
    st.subheader("⚙️ Recipe Settings")

    with st.form("edit_recipe"):
        col1, col2 = st.columns(2)
        with col1:
            name           = st.text_input("Menu Item Name",     value=recipe.get("name", ""))
            category       = st.text_input("Category",           value=recipe.get("category", ""))
            component_name = st.text_input("Component / Venue",  value=recipe.get("component_name", ""))
            updated_by     = st.text_input("Updated By",         value=recipe.get("updated_by", "") or "")
        with col2:
            selling_price  = st.number_input(
                "Selling Price ($)", value=float(recipe.get("selling_price") or 0), format="%.2f"
            )
            cost_pct_goal  = st.number_input(
                "Cost % Goal", value=float(recipe.get("cost_pct_goal") or 0.17),
                format="%.4f", min_value=0.01, max_value=1.0,
            )
            servings = st.number_input(
                "Servings Per Portion", value=int(recipe.get("servings_per_portion") or 1), min_value=1
            )
            portions = st.number_input(
                "Portions", value=int(recipe.get("portions") or 1), min_value=1
            )
        notes = st.text_area("Preparation Notes", value=recipe.get("notes", "") or "", height=150)

        if st.form_submit_button("💾 Save Settings", type="primary"):
            pca_engine.update_recipe(recipe_id, {
                "name":                name.strip(),
                "category":            category,
                "component_name":      component_name,
                "selling_price":       selling_price,
                "cost_pct_goal":       cost_pct_goal,
                "servings_per_portion": int(servings),
                "portions":            int(portions),
                "updated_by":          updated_by,
                "notes":               notes,
            })
            st.success("✅ Settings saved.")
            st.rerun()

    st.markdown("---")
    col_dup, col_del = st.columns(2)
    with col_dup:
        new_name = st.text_input("New name for duplicate", value=f"{recipe.get('name','')} (Copy)")
        if st.button("📋 Duplicate Recipe"):
            new_id = pca_engine.duplicate_recipe(recipe_id, new_name=new_name.strip() or None)
            st.success(f"Duplicated!")
            st.session_state["pca_recipe_id"] = new_id
            st.rerun()
    with col_del:
        st.markdown("**⚠️ Danger Zone**")
        if st.button("🗄️ Archive Recipe", type="secondary"):
            pca_engine.delete_recipe(recipe_id, soft=True)
            st.session_state["pca_recipe_id"] = None
            st.success("Recipe archived.")
            st.rerun()


# ────────────────────────────────────────────────────────────────────
# MAIN NAV
# ────────────────────────────────────────────────────────────────────
def main():
    with st.sidebar:
        # External st.image() removed — caused raw HTML injection on Cloud
        st.markdown("## 🏟️ UHA Inventory")
        st.markdown("---")
        page = st.radio("Navigate", [
            "🏠 Dashboard",
            "📦 Inventory",
            "📥 Import",
            "🏷️ GL Codes",
            "📜 History",
            "🍽️ PCA",
            "📤 Export",
        ])

    onedrive_auth_sidebar()

    if   page == "🏠 Dashboard": page_dashboard()
    elif page == "📦 Inventory": page_inventory()
    elif page == "📥 Import":    page_import()
    elif page == "🏷️ GL Codes":  page_gl_codes()
    elif page == "📜 History":   page_history()
    elif page == "🍽️ PCA":       page_pca()
    elif page == "📤 Export":    page_export()


if __name__ == "__main__":
    main()
