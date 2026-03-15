"""
pca_engine.py — Portion Cost Analysis (PCA) Engine

Depends on: database.get_conn (InventoryDatabase provides DB helpers)
Assumes recipes and recipe_ingredients tables are created by database.py.
"""

import os
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import psycopg2.extras

from database import get_conn

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
INGREDIENT_TYPE_FOOD = "food"
INGREDIENT_TYPE_DISPOSABLE = "disposable"

UNIT_ALIASES = {
    "OZ": "Ounce",
    "OUNCE": "Ounce",
    "LB": "Pound",
    "POUND": "Pound",
    "EA": "Each",
    "EACH": "Each",
    "CS": "Case",
    "CASE": "Case",
    "SLEVE": "Sleeve",
    "SLV": "Sleeve",
}

# ---------------------------------------------------------------------
# Calculation helpers
# ---------------------------------------------------------------------
def _safe_float(v: Any, default: float = 0.0) -> float:
    """
    Convert a DB or user value to float.
    Return `default` only for None or invalid values. Preserve valid zeros.
    """
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def calc_unit_cost(invoice_amount: float, conv_ratio: float, yield_pct: float) -> float:
    """
    Cost per single unit after conversion and yield adjustment.

    Formula: invoice_amount / conv_ratio / yield_pct
    """
    ratio = _safe_float(conv_ratio, 1.0)
    yield_ = _safe_float(yield_pct, 1.0)
    amount = _safe_float(invoice_amount, 0.0)
    if ratio == 0 or yield_ == 0:
        return 0.0
    return amount / ratio / yield_


def calc_ep_cost(unit_cost: float, ep_amount: float) -> float:
    """Edible Portion cost = unit cost × portioned amount."""
    return unit_cost * _safe_float(ep_amount, 0.0)


def calc_product_cost_pct(cost_per_portion: float, selling_price: float) -> float:
    """Actual product cost percentage."""
    sp = _safe_float(selling_price, 1.0)
    if sp == 0:
        return 0.0
    return cost_per_portion / sp


def calc_per_serving_cost_goal(selling_price: float, cost_pct_goal: float) -> float:
    """Maximum allowable cost per serving based on the target cost %."""
    return _safe_float(selling_price, 0.0) * _safe_float(cost_pct_goal, 0.17)


# ---------------------------------------------------------------------
# PCA Engine
# ---------------------------------------------------------------------
class PCAEngine:
    """
    PCAEngine provides recipe CRUD, ingredient line CRUD, cost calculations,
    AI suggestion stubs, and utility helpers.
    """

    def __init__(self, db):
        """
        db: InventoryDatabase instance (used for item lookups).
        PCAEngine does not create recipe tables here to avoid schema duplication.
        """
        self.db = db

    # -----------------------------------------------------------------
    # Recipe CRUD
    # -----------------------------------------------------------------
    def create_recipe(
        self,
        name: str,
        category: str = "Concessions",
        component_name: str = "Default",
        selling_price: float = 0.0,
        cost_pct_goal: float = 0.17,
        servings_per_portion: int = 1,
        portions: int = 1,
        recipe_date: Optional[str] = None,
        updated_by: str = "",
        notes: str = "",
    ) -> Optional[int]:
        """Insert a new recipe and return its recipe_id."""
        recipe_date_val = recipe_date or date.today().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO recipes
                    (name, category, component_name, selling_price, cost_pct_goal,
                     servings_per_portion, portions, recipe_date, updated_by, notes,
                     created_at, last_updated)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                RETURNING recipe_id
                """,
                (
                    name.strip(),
                    category,
                    component_name,
                    selling_price,
                    cost_pct_goal,
                    servings_per_portion,
                    portions,
                    recipe_date_val,
                    updated_by,
                    notes,
                ),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None

    def get_recipe(self, recipe_id: int) -> Optional[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM recipes WHERE recipe_id = %s", (recipe_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_recipes(self, include_archived: bool = False) -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if include_archived:
                cur.execute("SELECT * FROM recipes ORDER BY name")
            else:
                cur.execute("SELECT * FROM recipes WHERE record_status = 'active' ORDER BY name")
            return [dict(r) for r in cur.fetchall()]

    def update_recipe(self, recipe_id: int, updates: Dict[str, Any]) -> bool:
        allowed = {
            "name",
            "category",
            "component_name",
            "selling_price",
            "cost_pct_goal",
            "servings_per_portion",
            "portions",
            "recipe_date",
            "updated_by",
            "notes",
            "record_status",
        }
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return False
        fields["last_updated"] = datetime.utcnow()
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE recipes SET {set_clause} WHERE recipe_id = %s", list(fields.values()) + [recipe_id])
            return cur.rowcount > 0

    def delete_recipe(self, recipe_id: int, soft: bool = True) -> bool:
        """Soft-delete by default (archive); set soft=False to hard-delete."""
        if soft:
            return self.update_recipe(recipe_id, {"record_status": "archived"})
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM recipes WHERE recipe_id = %s", (recipe_id,))
            return cur.rowcount > 0

    # -----------------------------------------------------------------
    # Ingredient Line CRUD
    # -----------------------------------------------------------------
    def add_ingredient(
        self,
        recipe_id: int,
        item_key: str,
        ep_amount: float = 1.0,
        unit: str = "Each",
        ingredient_type: str = INGREDIENT_TYPE_FOOD,
        sort_order: int = 0,
        notes: str = "",
    ) -> Optional[int]:
        """Append an ingredient line and return its line_id."""
        if sort_order == 0:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT COALESCE(MAX(sort_order), 0) + 1
                    FROM recipe_ingredients
                    WHERE recipe_id = %s AND ingredient_type = %s
                    """,
                    (recipe_id, ingredient_type),
                )
                sort_order = cur.fetchone()[0] or 1
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO recipe_ingredients
                    (recipe_id, item_key, ingredient_type, ep_amount, unit, sort_order, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING line_id
                """,
                (recipe_id, item_key, ingredient_type, ep_amount, unit, sort_order, notes),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None

    def update_ingredient(self, line_id: int, updates: Dict[str, Any]) -> bool:
        allowed = {"item_key", "ep_amount", "unit", "ingredient_type", "sort_order", "notes"}
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return False
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE recipe_ingredients SET {set_clause} WHERE line_id = %s", list(fields.values()) + [line_id])
            return cur.rowcount > 0

    def remove_ingredient(self, line_id: int) -> bool:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM recipe_ingredients WHERE line_id = %s", (line_id,))
            return cur.rowcount > 0

    def get_recipe_lines(self, recipe_id: int) -> List[Dict]:
        """Return ingredient lines enriched with live item data from items table."""
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                """
                SELECT
                    ri.line_id,
                    ri.recipe_id,
                    ri.item_key,
                    ri.ingredient_type,
                    ri.ep_amount,
                    ri.unit,
                    ri.sort_order,
                    ri.notes                                    AS line_notes,
                    i.description,
                    COALESCE(i.cost, 0)                         AS invoice_amount,
                    COALESCE(i.override_conv_ratio, i.conv_ratio, 1.0) AS conv_ratio,
                    COALESCE(i.override_yield, i.yield, 1.0)   AS yield_pct,
                    COALESCE(i.override_vendor, i.vendor, '')   AS vendor,
                    i.pack_type,
                    i.gl_code,
                    i.gl_name
                FROM recipe_ingredients ri
                LEFT JOIN items i ON i.key = ri.item_key
                WHERE ri.recipe_id = %s
                ORDER BY ri.ingredient_type, ri.sort_order
                """,
                (recipe_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    # -----------------------------------------------------------------
    # Cost Calculation
    # -----------------------------------------------------------------
    def calculate_pca(self, recipe_id: int) -> Dict[str, Any]:
        """
        Compute the full Portion Cost Analysis for a recipe.
        Returns a dict with recipe header, lines, totals, and metrics.
        """
        recipe = self.get_recipe(recipe_id)
        if not recipe:
            return {}

        lines = self.get_recipe_lines(recipe_id)

        servings = max(1, int(recipe.get("servings_per_portion") or 1))
        portions = max(1, int(recipe.get("portions") or 1))
        selling_price = _safe_float(recipe.get("selling_price"), 0.01)
        cost_pct_goal = _safe_float(recipe.get("cost_pct_goal"), 0.17)

        food_lines: List[Dict] = []
        disposable_lines: List[Dict] = []

        for line in lines:
            invoice_amt = _safe_float(line.get("invoice_amount"))
            conv_ratio = _safe_float(line.get("conv_ratio"), 1.0)
            yield_pct = _safe_float(line.get("yield_pct"), 1.0)
            ep_amount = _safe_float(line.get("ep_amount"), 1.0)

            unit_cost = calc_unit_cost(invoice_amt, conv_ratio, yield_pct)
            ep_cost = calc_ep_cost(unit_cost, ep_amount)
            es_amount = ep_amount / servings
            es_cost = calc_ep_cost(unit_cost, es_amount)

            enriched = {
                **line,
                "unit_cost": round(unit_cost, 4),
                "ep_cost": round(ep_cost, 4),
                "es_amount": round(es_amount, 4),
                "es_cost": round(es_cost, 4),
            }

            if line.get("ingredient_type") == INGREDIENT_TYPE_FOOD:
                food_lines.append(enriched)
            else:
                enriched["cost_per_portion"] = round(ep_cost, 4)
                enriched["cost_per_serving"] = round(ep_cost / servings, 4)
                disposable_lines.append(enriched)

        total_food_cost = round(sum(l["ep_cost"] for l in food_lines), 4)
        total_disposable_cost = round(sum(l.get("cost_per_portion", 0) for l in disposable_lines), 4)
        cost_per_portion = round(total_food_cost + total_disposable_cost, 4)
        cost_per_serving = round(cost_per_portion / servings, 4)
        product_cost_pct = calc_product_cost_pct(cost_per_portion, selling_price)
        per_serving_cost_goal = calc_per_serving_cost_goal(selling_price, cost_pct_goal)

        over_goal = product_cost_pct > cost_pct_goal
        status = "over" if over_goal else "on_target"
        pct_diff = round(product_cost_pct - cost_pct_goal, 4)

        return {
            "recipe": recipe,
            "food_lines": food_lines,
            "disposable_lines": disposable_lines,
            "totals": {
                "total_food_cost": total_food_cost,
                "total_disposable_cost": total_disposable_cost,
                "cost_per_portion": cost_per_portion,
                "cost_per_serving": cost_per_serving,
            },
            "metrics": {
                "selling_price": selling_price,
                "cost_pct_goal": cost_pct_goal,
                "product_cost_pct": round(product_cost_pct, 4),
                "per_serving_cost_goal": round(per_serving_cost_goal, 4),
                "pct_diff": pct_diff,
                "status": status,
                "over_goal": over_goal,
            },
        }

    # -----------------------------------------------------------------
    # AI Alternative Suggestions (safe, testable stubs)
    # -----------------------------------------------------------------
    def generate_ai_suggestions(
        self,
        recipe_id: int,
        max_suggestions: int = 8,
        api_key: Optional[str] = None,
    ) -> List[Dict]:
        """
        Suggest alternative ingredients using an LLM. Returns [] if no API key.
        This method uses a safe placeholder implementation; replace _call_anthropic
        with a real client call when ready.
        """
        pca = self.calculate_pca(recipe_id)
        if not pca:
            return []

        recipe = pca["recipe"]
        metrics = pca["metrics"]

        inventory_items = self._get_inventory_for_suggestions()

        ingredient_summary = []
        for line in pca["food_lines"] + pca["disposable_lines"]:
            ingredient_summary.append({
                "item": line.get("description", line.get("item_key", "")),
                "type": line.get("ingredient_type"),
                "ep_amount": line.get("ep_amount"),
                "unit": line.get("unit"),
                "ep_cost": line.get("ep_cost") or line.get("cost_per_portion", 0),
                "vendor": line.get("vendor", ""),
            })

        prompt = self._build_suggestion_prompt(
            recipe_name=recipe.get("name", ""),
            selling_price=metrics["selling_price"],
            cost_pct_goal=metrics["cost_pct_goal"],
            current_pct=metrics["product_cost_pct"],
            cost_per_portion=pca["totals"]["cost_per_portion"],
            ingredients=ingredient_summary,
            inventory=inventory_items,
            max_suggestions=max_suggestions,
        )

        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY")
        if not key:
            return []

        try:
            response_text = self._call_anthropic(prompt, api_key=key)
            suggestions = self._parse_suggestions(response_text, pca)
            return suggestions[:max_suggestions]
        except Exception as exc:
            print(f"[PCAEngine] AI suggestion error: {exc}")
            return []

    def _get_inventory_for_suggestions(self) -> List[Dict]:
        """
        Pull a compact inventory list for the AI prompt (active items only).
        Uses self.db.get_all_items() if available for consistency.
        """
        try:
            if self.db:
                items = self.db.get_all_items(record_status="active")
                compact = []
                for it in items:
                    compact.append({
                        "key": it.get("key"),
                        "description": it.get("description"),
                        "pack_type": it.get("pack_type"),
                        "cost": float(it.get("cost") or 0),
                        "conv_ratio": float(it.get("conv_ratio") or 1.0),
                        "yield_pct": float(it.get("yield") or 1.0),
                        "vendor": it.get("vendor") or "",
                        "gl_code": it.get("gl_code") or "",
                        "gl_name": it.get("gl_name") or "",
                    })
                return compact
        except Exception:
            pass

        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                """
                SELECT key, description, pack_type,
                       COALESCE(cost,0) AS cost,
                       COALESCE(conv_ratio,1.0) AS conv_ratio,
                       COALESCE(yield,1.0) AS yield_pct,
                       COALESCE(vendor,'') AS vendor,
                       gl_code, gl_name
                FROM items
                WHERE record_status = 'active'
                ORDER BY description
                LIMIT 1000
                """
            )
            return [dict(r) for r in cur.fetchall()]

    def _build_suggestion_prompt(
        self,
        recipe_name: str,
        selling_price: float,
        cost_pct_goal: float,
        current_pct: float,
        cost_per_portion: float,
        ingredients: List[Dict],
        inventory: List[Dict],
        max_suggestions: int,
    ) -> str:
        ing_text = "\n".join(
            f"  - {i['item']} | {i['ep_amount']} {i['unit']} | Cost: ${float(i['ep_cost']):.4f} | Vendor: {i.get('vendor','')}"
            for i in ingredients
        )

        inv_text = "\n".join(
            f"  KEY={item['key']} | {item['description']} | Pack: {item['pack_type']} | Cost: ${float(item['cost']):.4f} | ConvRatio: {item['conv_ratio']} | Yield: {item['yield_pct']} | Vendor: {item['vendor']}"
            for item in inventory[:300]
        )

        return (
            f"You are a food cost analyst for a stadium concession operation.\n\n"
            f"MENU ITEM: {recipe_name}\n"
            f"SELLING PRICE: ${selling_price:.2f}\n"
            f"COST % GOAL: {cost_pct_goal * 100:.1f}%\n"
            f"CURRENT COST %: {current_pct * 100:.2f}%\n"
            f"CURRENT COST PER PORTION: ${cost_per_portion:.4f}\n\n"
            f"CURRENT INGREDIENTS:\n{ing_text}\n\n"
            f"AVAILABLE INVENTORY ITEMS:\n{inv_text}\n\n"
            f"TASK: Suggest up to {max_suggestions} alternative ingredient substitutions that reduce cost per portion while preserving menu intent. For each suggestion, return: ingredient_to_replace, alternate_item_key (if available), alternate_item_description, alternate_vendor, alternate_cost_per_portion, alternate_cost_variance, estimated_product_cost_pct.\n"
        )

    def _call_anthropic(self, prompt: str, api_key: str) -> str:
        """
        Placeholder for Anthropic/OpenAI call.
        Replace with a real client call in production.
        Returns a deterministic JSON string for testing.
        """
        placeholder = json.dumps({
            "suggestions": [
                {
                    "ingredient_to_replace": "Example Ingredient",
                    "alternate_item_key": None,
                    "alternate_item_description": "Lower-cost Example",
                    "alternate_vendor": "Vendor A",
                    "alternate_cost_per_portion": 0.12,
                    "alternate_cost_variance": -0.05,
                    "estimated_product_cost_pct": 0.18
                }
            ]
        })
        return placeholder

    def _parse_suggestions(self, response_text: str, pca: Dict) -> List[Dict]:
        """
        Parse the LLM response into the expected list of suggestion dicts.
        Expects JSON with a top-level 'suggestions' array.
        """
        try:
            data = json.loads(response_text)
            suggestions = data.get("suggestions", [])
            out = []
            for s in suggestions:
                alt_cost = float(s.get("alternate_cost_per_portion") or 0)
                orig = next((l for l in (pca.get("food_lines") or []) if l.get("description") == s.get("ingredient_to_replace")), None)
                orig_cost = float(orig.get("ep_cost") if orig else 0)
                variance = alt_cost - orig_cost
                out.append({
                    "ingredient_to_replace": s.get("ingredient_to_replace"),
                    "alternate_item_key": s.get("alternate_item_key"),
                    "alternate_item_description": s.get("alternate_item_description"),
                    "alternate_vendor": s.get("alternate_vendor"),
                    "alternate_cost_per_portion": round(alt_cost, 6),
                    "alternate_cost_variance": round(variance, 6),
                    "product_cost_pct_effect": s.get("estimated_product_cost_pct"),
                })
            return out
        except Exception:
            return []

    # -----------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------
    def duplicate_recipe(self, recipe_id: int, new_name: Optional[str] = None) -> int:
        """Clone a recipe (header + all lines) and return the new recipe_id."""
        original = self.get_recipe(recipe_id)
        if not original:
            raise ValueError(f"Recipe {recipe_id} not found.")
        lines = self.get_recipe_lines(recipe_id)

        name = new_name or f"{original['name']} (Copy)"
        new_id = self.create_recipe(
            name=name,
            category=original.get("category", ""),
            component_name=original.get("component_name", ""),
            selling_price=float(original.get("selling_price") or 0),
            cost_pct_goal=float(original.get("cost_pct_goal") or 0.17),
            servings_per_portion=int(original.get("servings_per_portion") or 1),
            portions=int(original.get("portions") or 1),
            recipe_date=str(original.get("recipe_date") or ""),
            updated_by=original.get("updated_by", ""),
            notes=original.get("notes", "") or "",
        )
        for line in lines:
            self.add_ingredient(
                recipe_id=new_id,
                item_key=line["item_key"],
                ep_amount=float(line.get("ep_amount") or 1),
                unit=line.get("unit", "Each"),
                ingredient_type=line.get("ingredient_type", INGREDIENT_TYPE_FOOD),
                sort_order=int(line.get("sort_order") or 0),
                notes=line.get("line_notes", "") or "",
            )
        return new_id

    def export_pca_dict(self, recipe_id: int) -> Dict:
        """Return the full PCA as a serialisable dict (for JSON export / printing)."""
        pca = self.calculate_pca(recipe_id)

        def _clean(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(i) for i in obj]
            return obj

        return _clean(pca)
 