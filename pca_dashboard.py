# pca_dashboard.py
"""
PCA Dashboard — Streamlit UI for Portion Cost Analysis

Dependencies:
  - streamlit
  - pandas
  - database.InventoryDatabase (as `db`)
  - pca_engine.PCAEngine (as `PCAEngine`)
  - auth (get_current_user, require_auth, render_user_badge, get_changed_by, why_prompt)

This file is a self-contained Streamlit page that:
  - Lists recipes
  - Shows PCA results for a selected recipe
  - Allows adding/removing ingredients
  - Duplicates and exports recipes
  - Triggers AI suggestions (uses PCAEngine.generate_ai_suggestions)
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
import json

import streamlit as st
import pandas as pd

from database import InventoryDatabase
from pca_engine import PCAEngine
import auth

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _format_currency(v: float) -> str:
    try:
        return f"${float(v):.4f}"
    except Exception:
        return "$0.0000"


def _safe_get(d: Dict, k: str, default=None):
    return d.get(k) if d and k in d else default


# ---------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------
def render_pca_dashboard(db: InventoryDatabase):
    """
    Main entrypoint for the PCA Dashboard page.
    Call this from your Streamlit app pages registry.
    """
    # Require auth and show badge
    if not auth.require_auth(db):
        return
    auth.render_user_badge()

    st.title("Portion Cost Analysis (PCA) Dashboard")
    st.markdown("Manage recipes, run cost analysis, and explore AI substitution suggestions.")

    pca = PCAEngine(db)

    # Sidebar: recipe selection and actions
    with st.sidebar:
        st.header("Recipes")
        recipes = pca.get_all_recipes()
        recipe_map = {r["name"]: r["recipe_id"] for r in recipes}
        recipe_names = sorted(recipe_map.keys())
        selected_name = st.selectbox("Select recipe", ["-- New / Select --"] + recipe_names, index=0)

        if selected_name and selected_name != "-- New / Select --":
            selected_id = recipe_map[selected_name]
        else:
            selected_id = None

        st.markdown("---")
        st.subheader("Actions")
        if st.button("Create New Recipe"):
            name = st.text_input("Recipe name", key="new_recipe_name")
            # show inline creation UI
            if name:
                rid = pca.create_recipe(name=name, updated_by=auth.get_changed_by())
                if rid:
                    st.success(f"Created recipe: {name}")
                    st.experimental_rerun()

        if selected_id:
            if st.button("Duplicate Recipe"):
                new_name = f"{selected_name} (Copy)"
                pca.duplicate_recipe(selected_id, new_name=new_name)
                st.success("Recipe duplicated.")
                st.experimental_rerun()

            if st.button("Export PCA JSON"):
                export = pca.export_pca_dict(selected_id)
                st.download_button(
                    "Download PCA JSON",
                    data=json.dumps(export, indent=2),
                    file_name=f"pca_{selected_id}.json",
                    mime="application/json",
                )

    # Main area
    col1, col2 = st.columns([2, 3])

    # Left: recipe header + ingredient editor
    with col1:
        st.subheader("Recipe Details")
        if selected_id:
            recipe = pca.get_recipe(selected_id)
            if not recipe:
                st.error("Selected recipe not found.")
                return
            st.text_input("Name", value=recipe.get("name", ""), key="recipe_name", disabled=True)
            st.text_input("Category", value=recipe.get("category", ""), key="recipe_category", disabled=True)
            st.number_input("Selling Price", value=float(recipe.get("selling_price") or 0.0), key="recipe_price", disabled=True)
            st.text_input("Component", value=recipe.get("component_name") or "", key="recipe_component", disabled=True)
            st.caption(f"Servings per portion: {recipe.get('servings_per_portion')} · Portions: {recipe.get('portions')}")

            st.markdown("### Ingredients")
            lines = pca.get_recipe_lines(selected_id)
            if not lines:
                st.info("No ingredients yet. Add one below.")
            else:
                df_rows = []
                for l in lines:
                    df_rows.append({
                        "line_id": l.get("line_id"),
                        "item_key": l.get("item_key"),
                        "description": l.get("description") or "",
                        "ep_amount": float(l.get("ep_amount") or 0),
                        "unit": l.get("unit") or "",
                        "type": l.get("ingredient_type") or "food",
                        "ep_cost": float(l.get("ep_cost") or 0),
                    })
                df = pd.DataFrame(df_rows)
                st.dataframe(df[["description", "item_key", "ep_amount", "unit", "type", "ep_cost"]], use_container_width=True)

            st.markdown("#### Add Ingredient")
            with st.form("add_ingredient_form", clear_on_submit=True):
                item_key = st.text_input("Item Key (exact)", placeholder="ITEM DESCRIPTION||PACK", key="add_item_key")
                ep_amount = st.number_input("EP Amount", min_value=0.0, value=1.0, step=0.1, key="add_ep_amount")
                unit = st.text_input("Unit", value="Each", key="add_unit")
                ing_type = st.selectbox("Type", [INGREDIENT_TYPE_FOOD, INGREDIENT_TYPE_DISPOSABLE], index=0, key="add_type")
                notes = st.text_area("Notes", key="add_notes", height=60)
                submitted = st.form_submit_button("Add Ingredient")
                if submitted:
                    if not item_key:
                        st.error("Item key is required.")
                    else:
                        line_id = pca.add_ingredient(
                            recipe_id=selected_id,
                            item_key=item_key,
                            ep_amount=ep_amount,
                            unit=unit,
                            ingredient_type=ing_type,
                            notes=notes,
                        )
                        if line_id:
                            st.success("Ingredient added.")
                            st.experimental_rerun()
                        else:
                            st.error("Failed to add ingredient. Check item key exists in inventory.")

            st.markdown("#### Remove Ingredient")
            with st.form("remove_ingredient_form"):
                remove_line = st.number_input("Line ID to remove", min_value=0, value=0, step=1, key="remove_line_id")
                remove_submit = st.form_submit_button("Remove")
                if remove_submit:
                    if remove_line <= 0:
                        st.warning("Enter a valid line_id from the table above.")
                    else:
                        ok = pca.remove_ingredient(remove_line)
                        if ok:
                            st.success("Ingredient removed.")
                            st.experimental_rerun()
                        else:
                            st.error("Could not remove ingredient. Verify line_id.")

        else:
            st.info("Select a recipe from the sidebar or create a new one.")

    # Right: PCA results and AI suggestions
    with col2:
        st.subheader("PCA Results")
        if selected_id:
            pca_result = pca.calculate_pca(selected_id)
            if not pca_result:
                st.info("No PCA available for this recipe.")
            else:
                totals = pca_result.get("totals", {})
                metrics = pca_result.get("metrics", {})
                st.metric("Cost per Portion", _format_currency(totals.get("cost_per_portion", 0)))
                st.metric("Cost per Serving", _format_currency(totals.get("cost_per_serving", 0)))
                st.metric("Product Cost %", f"{metrics.get('product_cost_pct', 0) * 100:.2f}%")
                st.markdown("**Ingredient Breakdown (food)**")
                food = pca_result.get("food_lines", [])
                if food:
                    df_food = pd.DataFrame([
                        {
                            "description": f.get("description") or f.get("item_key"),
                            "ep_amount": f.get("ep_amount"),
                            "unit_cost": f.get("unit_cost"),
                            "ep_cost": f.get("ep_cost"),
                            "es_cost": f.get("es_cost"),
                        } for f in food
                    ])
                    st.dataframe(df_food, use_container_width=True)
                else:
                    st.info("No food ingredients.")

                st.markdown("**Disposables**")
                disp = pca_result.get("disposable_lines", [])
                if disp:
                    df_disp = pd.DataFrame([
                        {
                            "description": d.get("description") or d.get("item_key"),
                            "cost_per_portion": d.get("cost_per_portion"),
                            "cost_per_serving": d.get("cost_per_serving"),
                        } for d in disp
                    ])
                    st.dataframe(df_disp, use_container_width=True)
                else:
                    st.info("No disposables.")

                st.markdown("---")
                st.subheader("AI Substitution Suggestions")
                st.caption("Uses your configured LLM key. If none is configured, suggestions will be empty.")
                with st.expander("Run AI suggestions"):
                    max_sugg = st.slider("Max suggestions", min_value=1, max_value=12, value=6)
                    api_key_input = st.text_input("LLM API Key (optional override)", type="password", placeholder="Leave blank to use env/st.secrets", key="ai_key")
                    if st.button("Generate Suggestions"):
                        with st.spinner("Generating suggestions..."):
                            try:
                                suggestions = pca.generate_ai_suggestions(selected_id, max_suggestions=max_sugg, api_key=api_key_input or None)
                                if not suggestions:
                                    st.info("No suggestions returned (no API key or model returned none).")
                                else:
                                    for s in suggestions:
                                        st.markdown(f"**Replace:** {s.get('ingredient_to_replace')}")
                                        st.markdown(f"- **With:** {s.get('alternate_description')} (`{s.get('alternate_item_key')}`)")
                                        st.markdown(f"- **Vendor:** {s.get('alternate_vendor')}")
                                        st.markdown(f"- **Reason:** {s.get('reason') or '—'}")
                                        st.markdown(f"- **Orig EP Cost:** {_format_currency(s.get('orig_ep_cost', 0))} → **Alt EP Cost:** {_format_currency(s.get('alt_ep_cost', 0))}")
                                        st.markdown(f"- **New cost per portion:** {_format_currency(s.get('alternate_cost_per_portion', 0))} · Product cost %: {s.get('product_cost_pct_effect')}")
                                        st.markdown("---")
                            except Exception as e:
                                st.error(f"AI suggestion failed: {e}")

    # Footer: quick diagnostics
    st.markdown("---")
    st.caption(f"PCA Dashboard · last loaded {datetime.utcnow().isoformat()} UTC")


# ---------------------------------------------------------------------
# If run as a script, create DB and render
# ---------------------------------------------------------------------
if __name__ == "__main__":
    # Minimal local dev bootstrap
    db = InventoryDatabase()
    render_pca_dashboard(db)
