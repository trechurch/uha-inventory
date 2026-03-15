"""
PCA Engine v1.0 — Portion Cost Analysis
Handles recipe storage, ingredient line CRUD, cost calculations,
and AI-powered alternative ingredient suggestions.

Depends on: database.py (InventoryDatabase + get_conn)
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from database import get_conn


# ────────────────────────────────────────────────────────────────────
# CONSTANTS
# ────────────────────────────────────────────────────────────────────

INGREDIENT_TYPE_FOOD       = "food"
INGREDIENT_TYPE_DISPOSABLE = "disposable"

# Unit aliases used for display
UNIT_ALIASES = {
    "OZ":    "Ounce",
    "OUNCE": "Ounce",
    "LB":    "Pound",
    "POUND": "Pound",
    "EA":    "Each",
    "EACH":  "Each",
    "CS":    "Case",
    "CASE":  "Case",
    "SLEVE": "Sleeve",
    "SLV":   "Sleeve",
}


# ────────────────────────────────────────────────────────────────────
# CALCULATION HELPERS
# ────────────────────────────────────────────────────────────────────

def _safe_float(v, default: float = 0.0) -> float:
    """Convert any DB value to float, returning default on None/zero."""
    try:
        result = float(v or 0)
        return result if result != 0 else default
    except (TypeError, ValueError):
        return default


def calc_unit_cost(invoice_amount: float, conv_ratio: float, yield_pct: float) -> float:
    """Cost per single unit after conversion and yield adjustment.

    Formula: invoice_amount / conv_ratio / yield_pct

    invoice_amount — what was paid for the whole pack
    conv_ratio     — number of the unit type in the pack  (e.g. 60 Each in a case)
    yield_pct      — usable fraction of the ingredient    (e.g. 0.95 = 95 % yield)
    """
    ratio = _safe_float(conv_ratio, 1.0)
    yield_ = _safe_float(yield_pct, 1.0)
    amount = _safe_float(invoice_amount, 0.0)
    return amount / ratio / yield_


def calc_ep_cost(unit_cost: float, ep_amount: float) -> float:
    """Edible Portion cost = unit cost × portioned amount."""
    return unit_cost * _safe_float(ep_amount, 0.0)


def calc_product_cost_pct(cost_per_portion: float, selling_price: float) -> float:
    """Actual product cost percentage."""
    sp = _safe_float(selling_price, 1.0)
    return cost_per_portion / sp


def calc_per_serving_cost_goal(selling_price: float, cost_pct_goal: float) -> float:
    """Maximum allowable cost per serving based on the target cost %."""
    return _safe_float(selling_price, 0.0) * _safe_float(cost_pct_goal, 0.17)


# ────────────────────────────────────────────────────────────────────
# PCA ENGINE
# ────────────────────────────────────────────────────────────────────

class PCAEngine:

    def __init__(self, db):
        """
        db — an InventoryDatabase instance (used for item lookups).
        The PCA engine manages its own tables but shares the connection
        pattern from database.py.
        """
        self.db = db
        self.create_tables()

    # ── Schema ────────────────────────────────────────────────────────

    def create_tables(self):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS recipes (
                    recipe_id          SERIAL PRIMARY KEY,
                    name               TEXT NOT NULL,
                    category           TEXT,
                    component_name     TEXT,
                    selling_price      NUMERIC(10,2) DEFAULT 0,
                    cost_pct_goal      NUMERIC(6,4)  DEFAULT 0.17,
                    servings_per_portion INTEGER     DEFAULT 1,
                    portions           INTEGER       DEFAULT 1,
                    recipe_date        DATE,
                    updated_by         TEXT,
                    notes              TEXT,
                    record_status      TEXT DEFAULT 'active',
                    created_at         TIMESTAMPTZ DEFAULT NOW(),
                    last_updated       TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS recipe_ingredients (
                    line_id            SERIAL PRIMARY KEY,
                    recipe_id          INTEGER REFERENCES recipes(recipe_id) ON DELETE CASCADE,
                    item_key           TEXT REFERENCES items(key) ON DELETE SET NULL,
                    ingredient_type    TEXT DEFAULT 'food',
                    ep_amount          NUMERIC(10,4) DEFAULT 1,
                    unit               TEXT DEFAULT 'Each',
                    sort_order         INTEGER DEFAULT 0,
                    notes              TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe_id
                    ON recipe_ingredients(recipe_id);
                CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_item_key
                    ON recipe_ingredients(item_key);
            """)

    # ── Recipe CRUD ───────────────────────────────────────────────────

    def create_recipe(
        self,
        name: str,
        category: str = "Concessions",
        component_name: str = "TDECU Stadium",
        selling_price: float = 0.0,
        cost_pct_goal: float = 0.17,
        servings_per_portion: int = 1,
        portions: int = 1,
        recipe_date: Optional[str] = None,
        updated_by: str = "",
        notes: str = "",
    ) -> int:
        """Insert a new recipe and return its recipe_id."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO recipes
                    (name, category, component_name, selling_price, cost_pct_goal,
                     servings_per_portion, portions, recipe_date, updated_by, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING recipe_id
            """, (
                name.strip(), category, component_name,
                selling_price, cost_pct_goal,
                servings_per_portion, portions,
                recipe_date or datetime.now().strftime("%Y-%m-%d"),
                updated_by, notes,
            ))
            return cur.fetchone()[0]

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
                cur.execute(
                    "SELECT * FROM recipes WHERE record_status = 'active' ORDER BY name"
                )
            return [dict(r) for r in cur.fetchall()]

    def update_recipe(self, recipe_id: int, updates: Dict) -> bool:
        allowed = {
            "name", "category", "component_name", "selling_price",
            "cost_pct_goal", "servings_per_portion", "portions",
            "recipe_date", "updated_by", "notes", "record_status",
        }
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return False
        fields["last_updated"] = datetime.utcnow()
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE recipes SET {set_clause} WHERE recipe_id = %s",
                list(fields.values()) + [recipe_id],
            )
            return cur.rowcount > 0

    def delete_recipe(self, recipe_id: int, soft: bool = True) -> bool:
        """Soft-delete by default (archive); set soft=False to hard-delete."""
        if soft:
            return self.update_recipe(recipe_id, {"record_status": "archived"})
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM recipes WHERE recipe_id = %s", (recipe_id,))
            return cur.rowcount > 0

    # ── Ingredient Line CRUD ──────────────────────────────────────────

    def add_ingredient(
        self,
        recipe_id: int,
        item_key: str,
        ep_amount: float = 1.0,
        unit: str = "Each",
        ingredient_type: str = INGREDIENT_TYPE_FOOD,
        sort_order: int = 0,
        notes: str = "",
    ) -> int:
        """Append an ingredient line and return its line_id."""
        # Auto-assign sort_order if not specified
        if sort_order == 0:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """SELECT COALESCE(MAX(sort_order), 0) + 1
                       FROM recipe_ingredients
                       WHERE recipe_id = %s AND ingredient_type = %s""",
                    (recipe_id, ingredient_type),
                )
                sort_order = cur.fetchone()[0]
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO recipe_ingredients
                    (recipe_id, item_key, ingredient_type, ep_amount, unit, sort_order, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING line_id
            """, (recipe_id, item_key, ingredient_type, ep_amount, unit, sort_order, notes))
            return cur.fetchone()[0]

    def update_ingredient(self, line_id: int, updates: Dict) -> bool:
        allowed = {"item_key", "ep_amount", "unit", "ingredient_type", "sort_order", "notes"}
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return False
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE recipe_ingredients SET {set_clause} WHERE line_id = %s",
                list(fields.values()) + [line_id],
            )
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
            cur.execute("""
                SELECT
                    ri.line_id,
                    ri.recipe_id,
                    ri.item_key,
                    ri.ingredient_type,
                    ri.ep_amount,
                    ri.unit,
                    ri.sort_order,
                    ri.notes                                    AS line_notes,

                    -- Live item fields
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
            """, (recipe_id,))
            return [dict(r) for r in cur.fetchall()]

    # ── Cost Calculation ──────────────────────────────────────────────

    def calculate_pca(self, recipe_id: int) -> Dict:
        """
        Compute the full Portion Cost Analysis for a recipe.

        Returns a dict with:
            recipe          — header fields
            food_lines      — list of enriched food ingredient rows with costs
            disposable_lines— list of enriched disposable rows with costs
            totals          — aggregate cost summary
            metrics         — cost %, goal comparison, traffic-light status
        """
        recipe = self.get_recipe(recipe_id)
        if not recipe:
            return {}

        lines = self.get_recipe_lines(recipe_id)

        servings     = max(1, int(recipe.get("servings_per_portion") or 1))
        portions     = max(1, int(recipe.get("portions") or 1))
        selling_price = _safe_float(recipe.get("selling_price"), 0.01)
        cost_pct_goal = _safe_float(recipe.get("cost_pct_goal"), 0.17)

        food_lines, disposable_lines = [], []

        for line in lines:
            invoice_amt = _safe_float(line["invoice_amount"])
            conv_ratio  = _safe_float(line["conv_ratio"], 1.0)
            yield_pct   = _safe_float(line["yield_pct"], 1.0)
            ep_amount   = _safe_float(line["ep_amount"], 1.0)

            unit_cost = calc_unit_cost(invoice_amt, conv_ratio, yield_pct)
            ep_cost   = calc_ep_cost(unit_cost, ep_amount)
            # ES amount equals EP amount when servings_per_portion = 1 (prototype behaviour)
            es_amount = ep_amount / servings
            es_cost   = calc_ep_cost(unit_cost, es_amount)

            enriched = {
                **line,
                "unit_cost":  round(unit_cost, 4),
                "ep_cost":    round(ep_cost, 4),
                "es_amount":  round(es_amount, 4),
                "es_cost":    round(es_cost, 4),
            }

            if line["ingredient_type"] == INGREDIENT_TYPE_FOOD:
                food_lines.append(enriched)
            else:
                # Disposables: cost_per_portion = unit_cost × units_per_portion
                enriched["cost_per_portion"] = round(ep_cost, 4)
                enriched["cost_per_serving"] = round(ep_cost / servings, 4)
                disposable_lines.append(enriched)

        total_food_cost       = round(sum(l["ep_cost"] for l in food_lines), 4)
        total_disposable_cost = round(sum(l["cost_per_portion"] for l in disposable_lines), 4)
        cost_per_portion      = round(total_food_cost + total_disposable_cost, 4)
        cost_per_serving      = round(cost_per_portion / servings, 4)
        product_cost_pct      = calc_product_cost_pct(cost_per_portion, selling_price)
        per_serving_cost_goal = calc_per_serving_cost_goal(selling_price, cost_pct_goal)

        over_goal = product_cost_pct > cost_pct_goal
        status    = "over"  if over_goal else "on_target"
        pct_diff  = round(product_cost_pct - cost_pct_goal, 4)

        return {
            "recipe":           recipe,
            "food_lines":       food_lines,
            "disposable_lines": disposable_lines,
            "totals": {
                "total_food_cost":        total_food_cost,
                "total_disposable_cost":  total_disposable_cost,
                "cost_per_portion":       cost_per_portion,
                "cost_per_serving":       cost_per_serving,
            },
            "metrics": {
                "selling_price":          selling_price,
                "cost_pct_goal":          cost_pct_goal,
                "product_cost_pct":       round(product_cost_pct, 4),
                "per_serving_cost_goal":  round(per_serving_cost_goal, 4),
                "pct_diff":               pct_diff,
                "status":                 status,
                "over_goal":              over_goal,
            },
        }

    # ── AI Alternative Suggestions ────────────────────────────────────

    def generate_ai_suggestions(
        self,
        recipe_id: int,
        max_suggestions: int = 8,
        api_key: Optional[str] = None,
    ) -> List[Dict]:
        """
        Use the Anthropic API to suggest alternative ingredients that could
        reduce the cost-per-portion while maintaining menu intent.

        Returns a list of dicts:
            ingredient_to_replace  — original item description
            alternate_item         — suggested replacement description
            alternate_vendor       — vendor for the suggested item
            alternate_cost_per_portion — calculated cost if swapped in
            alternate_cost_variance    — difference vs original (negative = cheaper)
            product_cost_pct_effect    — new overall product cost %
        """
        pca = self.calculate_pca(recipe_id)
        if not pca:
            return []

        recipe  = pca["recipe"]
        metrics = pca["metrics"]

        # Build a compact inventory snapshot for the prompt
        inventory_items = self._get_inventory_for_suggestions()

        # Build ingredient summary for the prompt
        ingredient_summary = []
        for line in pca["food_lines"] + pca["disposable_lines"]:
            ingredient_summary.append({
                "item":        line.get("description", line.get("item_key", "")),
                "type":        line["ingredient_type"],
                "ep_amount":   line["ep_amount"],
                "unit":        line["unit"],
                "ep_cost":     line.get("ep_cost") or line.get("cost_per_portion", 0),
                "vendor":      line.get("vendor", ""),
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

        try:
            response_text = self._call_anthropic(prompt, api_key=api_key)
            suggestions   = self._parse_suggestions(response_text, pca)
            return suggestions
        except Exception as e:
            print(f"[PCAEngine] AI suggestion error: {e}")
            return []

    def _get_inventory_for_suggestions(self) -> List[Dict]:
        """Pull a compact inventory list for the AI prompt (active items only)."""
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT
                    key,
                    description,
                    pack_type,
                    COALESCE(cost, 0)                              AS cost,
                    COALESCE(override_conv_ratio, conv_ratio, 1.0) AS conv_ratio,
                    COALESCE(override_yield, yield, 1.0)           AS yield_pct,
                    COALESCE(override_vendor, vendor, '')           AS vendor,
                    gl_code,
                    gl_name
                FROM items
                WHERE record_status = 'active'
                ORDER BY description
            """)
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
            f"  - {i['item']} | {i['ep_amount']} {i['unit']} | "
            f"Cost: ${i['ep_cost']:.4f} | Vendor: {i['vendor']}"
            for i in ingredients
        )

        inv_text = "\n".join(
            f"  KEY={item['key']} | {item['description']} | "
            f"Pack: {item['pack_type']} | Cost: ${item['cost']:.4f} | "
            f"ConvRatio: {item['conv_ratio']} | Yield: {item['yield_pct']} | "
            f"Vendor: {item['vendor']}"
            for item in inventory[:300]  # cap prompt size
        )

        return f"""You are a food cost analyst for a stadium concession operation.

MENU ITEM: {recipe_name}
SELLING PRICE: ${selling_price:.2f}
COST % GOAL: {cost_pct_goal * 100:.1f}%
CURRENT COST %: {current_pct * 100:.2f}%
CURRENT COST PER PORTION: ${cost_per_portion:.4f}

CURRENT INGREDIENTS:
{ing_text}

AVAILABLE INVENTORY ITEMS:
{inv_text}

TASK:
Identify up to {max_suggestions} ingredient swaps from the AVAILABLE INVENTORY that would reduce the
cost per portion while keeping the menu item commercially viable.
For each suggestion provide exactly the following JSON array (no other text, no markdown fences):

[
  {{
    "ingredient_to_replace": "<exact description from CURRENT INGREDIENTS>",
    "alternate_item_key": "<exact KEY from AVAILABLE INVENTORY>",
    "alternate_description": "<description from AVAILABLE INVENTORY>",
    "alternate_vendor": "<vendor from AVAILABLE INVENTORY>",
    "reason": "<1-sentence rationale>"
  }}
]

Only suggest swaps where the replacement item is a plausible substitute (similar category, similar use).
Do NOT suggest swapping an item with itself.
Return ONLY the JSON array."""

    def _call_anthropic(self, prompt: str, api_key: Optional[str] = None) -> str:
        """Send the prompt to the Anthropic API and return the response text."""
        import urllib.request
        import json as _json

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            try:
                import streamlit as st
                key = st.secrets.get("ANTHROPIC_API_KEY", "")
            except Exception:
                pass
        if not key:
            raise ValueError("No ANTHROPIC_API_KEY found in secrets or environment.")

        payload = _json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key":         key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
        return data["content"][0]["text"]

    def _parse_suggestions(self, response_text: str, pca: Dict) -> List[Dict]:
        """
        Parse the AI JSON response and enrich each suggestion with cost impact.
        """
        try:
            # Strip any accidental markdown fences
            clean = re.sub(r"```[a-z]*", "", response_text).strip()
            raw   = json.loads(clean)
        except Exception:
            return []

        selling_price   = pca["metrics"]["selling_price"]
        cost_per_portion = pca["totals"]["cost_per_portion"]
        all_lines       = pca["food_lines"] + pca["disposable_lines"]

        # Build a quick lookup: description → line
        line_by_desc = {
            (l.get("description") or "").upper(): l for l in all_lines
        }

        # Build inventory lookup by key
        inventory_map: Dict[str, Dict] = {}
        for item in self._get_inventory_for_suggestions():
            inventory_map[item["key"]] = item

        results = []
        for suggestion in raw:
            orig_desc = suggestion.get("ingredient_to_replace", "")
            alt_key   = suggestion.get("alternate_item_key", "")
            alt_item  = inventory_map.get(alt_key)
            if not alt_item:
                continue

            # Find original line
            orig_line = line_by_desc.get(orig_desc.upper())
            if not orig_line:
                continue

            # Calculate alternative cost
            alt_unit_cost = calc_unit_cost(
                _safe_float(alt_item["cost"]),
                _safe_float(alt_item["conv_ratio"], 1.0),
                _safe_float(alt_item["yield_pct"], 1.0),
            )
            orig_ep_cost = float(orig_line.get("ep_cost") or orig_line.get("cost_per_portion") or 0)
            alt_ep_cost  = calc_ep_cost(alt_unit_cost, _safe_float(orig_line["ep_amount"], 1.0))

            alt_cost_per_portion = round(cost_per_portion - orig_ep_cost + alt_ep_cost, 4)
            alt_product_cost_pct = calc_product_cost_pct(alt_cost_per_portion, selling_price)
            variance             = round(alt_ep_cost - orig_ep_cost, 4)

            results.append({
                "ingredient_to_replace":       orig_desc,
                "alternate_description":       suggestion.get("alternate_description", ""),
                "alternate_vendor":            suggestion.get("alternate_vendor", alt_item.get("vendor", "")),
                "alternate_item_key":          alt_key,
                "reason":                      suggestion.get("reason", ""),
                "orig_ep_cost":                round(orig_ep_cost, 4),
                "alt_ep_cost":                 round(alt_ep_cost, 4),
                "alternate_cost_per_portion":  alt_cost_per_portion,
                "alternate_cost_variance":     variance,
                "product_cost_pct_effect":     round(alt_product_cost_pct, 4),
            })

        # Sort: cheapest alternatives first
        results.sort(key=lambda r: r["alternate_cost_per_portion"])
        return results

    # ── Utility ───────────────────────────────────────────────────────

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
                ep_amount=float(line["ep_amount"] or 1),
                unit=line.get("unit", "Each"),
                ingredient_type=line.get("ingredient_type", INGREDIENT_TYPE_FOOD),
                sort_order=int(line.get("sort_order") or 0),
                notes=line.get("line_notes", "") or "",
            )
        return new_id

    def export_pca_dict(self, recipe_id: int) -> Dict:
        """Return the full PCA as a serialisable dict (for JSON export / printing)."""
        pca = self.calculate_pca(recipe_id)
        # Convert any datetime objects to strings
        def _clean(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(i) for i in obj]
            return obj
        return _clean(pca)
