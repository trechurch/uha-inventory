"""
Inventory Database - PostgreSQL/Supabase version
Drop-in replacement for SQLite database.py
Connection string loaded from environment / Streamlit secrets

v4.0.1 — Added:
  - import_log table (count import audit trail)
  - update_quantity_from_count() — called by count_importer.commit_count()
  - log_count_import()           — called by count_importer.commit_count()

v4.0.0 — Added:
  - is_manual_override / manual_notes on items (migration-safe)
  - inventory_transactions table (full operational audit trail)
  - recipes + recipe_ingredients tables (PCA engine foundation)
  - _run_migrations() for safe ALTER TABLE on existing databases
  - set_manual_override / clear_manual_override methods
  - log_transaction / get_transactions methods
  - add_recipe / get_recipe / get_all_recipes / upsert_recipe / delete_recipe
  - add_recipe_ingredient / get_recipe_ingredients / delete_recipe_ingredient
  - get_all_descriptions() for fuzzy matching in importer
  - update_item_smart now respects is_manual_override flag
"""

import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from contextlib import contextmanager


def get_connection_string() -> str:
    """Get DB URL from Streamlit secrets or environment variable."""
    try:
        import streamlit as st
        return st.secrets["SUPABASE_DB_URL"]
    except Exception:
        return os.environ.get("SUPABASE_DB_URL", "")


@contextmanager
def get_conn():
    """Context manager — opens and closes a connection per operation."""
    conn = psycopg2.connect(get_connection_string())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class InventoryDatabase:

    def __init__(self, db_url: str = None):
        """db_url optional — falls back to secrets/env if not provided."""
        if db_url:
            os.environ["SUPABASE_DB_URL"] = db_url
        self.create_tables()
        self._run_migrations()

    # ------------------------------------------------------------------
    # SCHEMA — base tables (CREATE IF NOT EXISTS is idempotent)
    # ------------------------------------------------------------------
    def create_tables(self):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    key                  TEXT PRIMARY KEY,
                    description          TEXT,
                    pack_type            TEXT,
                    cost                 NUMERIC(10,4) DEFAULT 0,
                    per                  TEXT,
                    conv_ratio           NUMERIC(10,4) DEFAULT 1.0,
                    unit                 TEXT,
                    vendor               TEXT,
                    item_number          TEXT,
                    mog                  TEXT,
                    spacer               TEXT,
                    brand                TEXT,
                    last_updated         TIMESTAMPTZ,
                    yield                NUMERIC(10,4) DEFAULT 1.0,
                    gl_code              TEXT,
                    gl_name              TEXT,
                    override_pack_type   TEXT,
                    override_yield       NUMERIC(10,4),
                    override_conv_ratio  NUMERIC(10,4),
                    override_vendor      TEXT,
                    override_item_number TEXT,
                    override_gl          TEXT,
                    status_tag           TEXT DEFAULT 'Standard',
                    quantity_on_hand     NUMERIC(10,4) DEFAULT 0,
                    reorder_point        NUMERIC(10,4) DEFAULT 0,
                    is_chargeable        BOOLEAN DEFAULT TRUE,
                    cost_center          TEXT,
                    record_status        TEXT DEFAULT 'active',
                    created_date         TIMESTAMPTZ DEFAULT NOW(),
                    user_notes           TEXT,
                    gtin                 TEXT
                );

                CREATE TABLE IF NOT EXISTS item_history (
                    history_id      SERIAL PRIMARY KEY,
                    item_key        TEXT REFERENCES items(key),
                    change_date     TIMESTAMPTZ DEFAULT NOW(),
                    change_type     TEXT,
                    field_changed   TEXT,
                    old_value       TEXT,
                    new_value       TEXT,
                    change_source   TEXT,
                    source_document TEXT,
                    changed_by      TEXT,
                    change_reason   TEXT,
                    metadata        JSONB
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    price_id    SERIAL PRIMARY KEY,
                    item_key    TEXT REFERENCES items(key),
                    price       NUMERIC(10,4),
                    doc_date    DATE,
                    source_file TEXT,
                    vendor      TEXT,
                    imported_at TIMESTAMPTZ DEFAULT NOW()
                );

                -- ── OPERATIONAL TRANSACTION LOG ──────────────────────────
                -- Every physical movement: receives, counts, issues,
                -- transfers, adjustments. QOH is derived from this table.
                CREATE TABLE IF NOT EXISTS inventory_transactions (
                    tx_id           SERIAL PRIMARY KEY,
                    item_key        TEXT REFERENCES items(key),
                    tx_date         TIMESTAMPTZ DEFAULT NOW(),
                    tx_type         TEXT NOT NULL,
                    quantity        NUMERIC(10,4) NOT NULL,
                    unit            TEXT,
                    cost            NUMERIC(10,4),
                    cost_center     TEXT,
                    gl_code         TEXT,
                    source_document TEXT,
                    changed_by      TEXT,
                    notes           TEXT
                );

                -- ── PCA / RECIPE ENGINE FOUNDATION ───────────────────────
                CREATE TABLE IF NOT EXISTS recipes (
                    recipe_id    SERIAL PRIMARY KEY,
                    menu_item    TEXT UNIQUE NOT NULL,
                    description  TEXT,
                    mog          TEXT DEFAULT 'Off Catalog Item',
                    total_yield  NUMERIC(10,4),
                    serving_size NUMERIC(10,4),
                    serving_unit TEXT,
                    sale_price   NUMERIC(10,4),
                    is_active    BOOLEAN DEFAULT TRUE,
                    created_date TIMESTAMPTZ DEFAULT NOW(),
                    last_updated TIMESTAMPTZ
                );

                CREATE TABLE IF NOT EXISTS recipe_ingredients (
                    ingredient_id   SERIAL PRIMARY KEY,
                    recipe_id       INTEGER REFERENCES recipes(recipe_id)
                                        ON DELETE CASCADE,
                    item_key        TEXT REFERENCES items(key),
                    qty_per_serving NUMERIC(10,4) NOT NULL,
                    unit            TEXT,
                    yield_adjusted  BOOLEAN DEFAULT TRUE,
                    notes           TEXT
                );

                -- ── COUNT IMPORT LOG ─────────────────────────────────────
                -- Written by count_importer.commit_count() after every
                -- successful count sheet commit.
                CREATE TABLE IF NOT EXISTS import_log (
                    import_id        TEXT PRIMARY KEY,
                    source_file      TEXT,
                    file_format      TEXT,
                    data_layout      TEXT,
                    count_type       TEXT,
                    count_date       DATE,
                    cost_center      TEXT,
                    imported_by      TEXT,
                    imported_at      TIMESTAMPTZ DEFAULT NOW(),
                    total_items      INTEGER DEFAULT 0,
                    items_changed    INTEGER DEFAULT 0,
                    items_flagged    INTEGER DEFAULT 0,
                    total_prev_value NUMERIC(12,4) DEFAULT 0,
                    total_new_value  NUMERIC(12,4) DEFAULT 0,
                    variance_value   NUMERIC(12,4) DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_import_log_date
                    ON import_log(count_date DESC);
                CREATE INDEX IF NOT EXISTS idx_import_log_cost_center
                    ON import_log(cost_center);

                -- ── COUNT OVERRIDES (existing) ────────────────────────────
                CREATE TABLE IF NOT EXISTS count_overrides (
                    override_id  SERIAL PRIMARY KEY,
                    item_key     TEXT,
                    cost_center  TEXT,
                    divisor      NUMERIC(10,4) NOT NULL DEFAULT 1,
                    notes        TEXT,
                    created_date TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (item_key, cost_center)
                );

                CREATE TABLE IF NOT EXISTS count_override_settings (
                    setting_key   TEXT PRIMARY KEY,
                    setting_value TEXT
                );

                -- ── INDEXES ──────────────────────────────────────────────
                CREATE INDEX IF NOT EXISTS idx_items_description
                    ON items(description);
                CREATE INDEX IF NOT EXISTS idx_items_gl_code
                    ON items(gl_code);
                CREATE INDEX IF NOT EXISTS idx_items_vendor
                    ON items(vendor);
                CREATE INDEX IF NOT EXISTS idx_history_item_key
                    ON item_history(item_key);
                CREATE INDEX IF NOT EXISTS idx_tx_item_key
                    ON inventory_transactions(item_key);
                CREATE INDEX IF NOT EXISTS idx_tx_type
                    ON inventory_transactions(tx_type);
                CREATE INDEX IF NOT EXISTS idx_tx_date
                    ON inventory_transactions(tx_date);
                CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe
                    ON recipe_ingredients(recipe_id);
                CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_item
                    ON recipe_ingredients(item_key);
            """)

    # ------------------------------------------------------------------
    # MIGRATIONS — safe ALTER TABLE for columns added after initial deploy
    # ADD COLUMN IF NOT EXISTS is idempotent; safe to run every startup.
    # ------------------------------------------------------------------
    def _run_migrations(self):
        """Add new columns to existing tables without dropping data."""
        migrations = [
            # items: is_manual_override flag
            """ALTER TABLE items
               ADD COLUMN IF NOT EXISTS is_manual_override
               BOOLEAN DEFAULT FALSE""",
            # items: manual_notes for documenting why override was set
            """ALTER TABLE items
               ADD COLUMN IF NOT EXISTS manual_notes
               TEXT""",
        ]
        with get_conn() as conn:
            cur = conn.cursor()
            for sql in migrations:
                try:
                    cur.execute(sql)
                except Exception as e:
                    # Log but don't crash — migration may already be applied
                    print(f"[migration] skipped: {e}")

    # ------------------------------------------------------------------
    # KEY BUILDER
    # ------------------------------------------------------------------
    @staticmethod
    def build_key(item_name: str, pack_type: str) -> Optional[str]:
        name = str(item_name or "").strip().upper()
        pack = str(pack_type or "").strip().upper()
        if not name:
            return None
        return f"{name}||{pack}" if pack else f"{name}||CASE"

    # ------------------------------------------------------------------
    # CRUD — ITEMS
    # ------------------------------------------------------------------
    def add_item(self, item_data: Dict[str, Any],
                 changed_by: str = "system") -> bool:
        now = datetime.utcnow()
        item_data.setdefault("created_date", now)
        item_data.setdefault("last_updated", now)
        item_data.setdefault("record_status", "active")
        item_data.setdefault("yield", 1.0)
        item_data.setdefault("conv_ratio", 1.0)
        item_data.setdefault("quantity_on_hand", 0)
        item_data.setdefault("is_chargeable", True)
        item_data.setdefault("status_tag", "Standard")
        item_data.setdefault("is_manual_override", False)

        cols = list(item_data.keys())
        vals = list(item_data.values())
        placeholders = ", ".join(["%s"] * len(cols))
        col_str = ", ".join(cols)
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO items ({col_str}) VALUES ({placeholders})",
                    vals
                )
            self._add_history(item_data["key"], "created", "all",
                              new_value="Item created",
                              change_source="import",
                              changed_by=changed_by)
            return True
        except psycopg2.errors.UniqueViolation:
            return False
        except Exception as e:
            print(f"Error adding item: {e}")
            return False

    def upsert_item(self, item_data: Dict[str, Any],
                    doc_date: str = None,
                    source_document: str = None,
                    changed_by: str = "import") -> str:
        key = item_data.get("key") or self.build_key(
            item_data.get("description", ""),
            item_data.get("pack_type", "")
        )
        if not key:
            return "skipped"
        item_data["key"] = key
        if self.item_exists(key):
            self.update_item_smart(key, item_data,
                                   doc_date=doc_date,
                                   source_document=source_document,
                                   changed_by=changed_by)
            return "updated"
        else:
            self.add_item(item_data, changed_by=changed_by)
            return "created"

    def get_item(self, key: str) -> Optional[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM items WHERE key = %s", (key,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_items(self, record_status: str = "active") -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if record_status:
                cur.execute(
                    "SELECT * FROM items WHERE record_status = %s ORDER BY description",
                    (record_status,)
                )
            else:
                cur.execute("SELECT * FROM items ORDER BY description")
            return [dict(r) for r in cur.fetchall()]

    def get_all_descriptions(self) -> Dict[str, str]:
        """
        Returns {description_upper: key} for all active items.
        Used by importer fuzzy matching — loaded once per import run.
        """
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT UPPER(description), key
                FROM items
                WHERE record_status = 'active'
                  AND description IS NOT NULL
            """)
            return {row[0]: row[1] for row in cur.fetchall()}

    def get_items_by_cost_center(self, cost_center: str) -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT * FROM items WHERE cost_center = %s AND record_status = 'active' ORDER BY description",
                (cost_center,)
            )
            return [dict(r) for r in cur.fetchall()]

    def get_low_stock_items(self) -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM items
                WHERE quantity_on_hand < reorder_point
                  AND record_status = 'active'
                  AND reorder_point > 0
                ORDER BY (reorder_point - quantity_on_hand) DESC
            """)
            return [dict(r) for r in cur.fetchall()]

    def get_inventory_value(self) -> float:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT SUM(quantity_on_hand * cost) FROM items WHERE record_status = 'active'"
            )
            result = cur.fetchone()[0]
            return float(result) if result else 0.0

    def search_items(self, term: str) -> List[Dict]:
        p = f"%{term.upper()}%"
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM items
                WHERE UPPER(key) LIKE %s
                   OR UPPER(description) LIKE %s
                   OR UPPER(vendor) LIKE %s
                   OR gl_code LIKE %s
                   OR UPPER(brand) LIKE %s
                ORDER BY description
            """, (p, p, p, p, p))
            return [dict(r) for r in cur.fetchall()]

    def count_items(self, record_status: str = None) -> int:
        with get_conn() as conn:
            cur = conn.cursor()
            if record_status:
                cur.execute(
                    "SELECT COUNT(*) FROM items WHERE record_status = %s",
                    (record_status,)
                )
            else:
                cur.execute("SELECT COUNT(*) FROM items")
            return cur.fetchone()[0]

    def item_exists(self, key: str) -> bool:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM items WHERE key = %s", (key,))
            return cur.fetchone() is not None

    def delete_item(self, key: str, changed_by: str = "system") -> bool:
        return self._apply_update(key, {"record_status": "discontinued"},
                                  change_source="manual_deletion",
                                  changed_by=changed_by)

    # ------------------------------------------------------------------
    # SMART UPDATE + OVERRIDES
    # ------------------------------------------------------------------
    def update_item_smart(self, key: str, incoming: Dict[str, Any],
                          doc_date: str = None,
                          source_document: str = None,
                          changed_by: str = "import") -> bool:
        current = self.get_item(key)
        if not current:
            return False
        updates: Dict[str, Any] = {}
        now = datetime.utcnow()

        # ── Cost always updates regardless of manual override flag ──
        if incoming.get("cost"):
            updates["cost"] = incoming["cost"]
            updates["status_tag"] = "✅ Updated Today"

        # ── QOH always updates (comes from PAC counts, not invoices) ──
        if "quantity_on_hand" in incoming:
            updates["quantity_on_hand"] = incoming["quantity_on_hand"]

        # ── If is_manual_override is set, protect ALL other fields ──
        if current.get("is_manual_override"):
            updates["last_updated"] = now
            if "cost" in updates and doc_date:
                self._add_price_history(key, updates["cost"], doc_date,
                                        source_document, incoming.get("vendor"))
            return self._apply_update(key, updates, change_source="import",
                                      source_document=source_document,
                                      changed_by=changed_by)

        # ── Standard field-level override logic (no global lock) ──
        if not current["override_yield"] and "yield" in incoming:
            updates["yield"] = incoming["yield"]
        if not current["override_conv_ratio"] and "conv_ratio" in incoming:
            updates["conv_ratio"] = incoming["conv_ratio"]
        if not current["override_pack_type"] and "pack_type" in incoming:
            updates["pack_type"] = incoming["pack_type"]
        if not current["override_vendor"] and "vendor" in incoming:
            updates["vendor"] = incoming["vendor"]
        if not current["override_gl"] and "gl_code" in incoming:
            updates["gl_code"] = incoming["gl_code"]
            updates["gl_name"] = incoming.get("gl_name", current["gl_name"])
        for f in ("per", "unit", "item_number", "mog", "brand", "gtin",
                  "is_chargeable", "cost_center"):
            if incoming.get(f) is not None:
                updates[f] = incoming[f]
        updates["last_updated"] = now

        if "cost" in updates and doc_date:
            self._add_price_history(key, updates["cost"], doc_date,
                                    source_document, incoming.get("vendor"))
        return self._apply_update(key, updates, change_source="import",
                                  source_document=source_document,
                                  changed_by=changed_by)

    def set_override(self, key: str, field: str, value: Any,
                     changed_by: str = "user") -> bool:
        override_map = {
            "pack_type":  "override_pack_type",
            "yield":      "override_yield",
            "conv_ratio": "override_conv_ratio",
            "vendor":     "override_vendor",
            "gl":         "override_gl",
        }
        if field not in override_map:
            return False
        return self._apply_update(key,
                                  {override_map[field]: value, field: value},
                                  change_source="manual_override",
                                  changed_by=changed_by)

    def clear_override(self, key: str, field: str,
                       changed_by: str = "user") -> bool:
        override_map = {
            "pack_type":  "override_pack_type",
            "yield":      "override_yield",
            "conv_ratio": "override_conv_ratio",
            "vendor":     "override_vendor",
            "gl":         "override_gl",
        }
        if field not in override_map:
            return False
        return self._apply_update(key, {override_map[field]: None},
                                  change_source="clear_override",
                                  changed_by=changed_by)

    def set_manual_override(self, key: str,
                            notes: str = None,
                            changed_by: str = "user") -> bool:
        """
        Set the global is_manual_override flag on an item.
        When set, all import runs will only update cost and QOH for this item —
        every other field is frozen until the flag is cleared.
        """
        updates = {
            "is_manual_override": True,
            "last_updated": datetime.utcnow(),
        }
        if notes is not None:
            updates["manual_notes"] = notes
        return self._apply_update(key, updates,
                                  change_source="manual_override",
                                  changed_by=changed_by)

    def clear_manual_override(self, key: str,
                              changed_by: str = "user") -> bool:
        """
        Remove the global manual override lock.
        The item will resume normal import update behavior.
        """
        return self._apply_update(
            key,
            {"is_manual_override": False,
             "manual_notes": None,
             "last_updated": datetime.utcnow()},
            change_source="clear_override",
            changed_by=changed_by
        )

    # ------------------------------------------------------------------
    # INVENTORY TRANSACTIONS
    # ------------------------------------------------------------------
    # tx_type values: 'PAC_COUNT', 'VENDOR_RECEIPT', 'PCA_ISSUE',
    #                 'TRANSFER_OUT', 'TRANSFER_IN', 'ADJUSTMENT', 'MANUAL'

    def log_transaction(self, item_key: str, tx_type: str,
                        quantity: float, unit: str = None,
                        cost: float = None, cost_center: str = None,
                        gl_code: str = None, source_document: str = None,
                        changed_by: str = "system",
                        notes: str = None) -> Optional[int]:
        """
        Write one row to inventory_transactions.
        Returns the new tx_id, or None on failure.
        """
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO inventory_transactions
                    (item_key, tx_type, quantity, unit, cost, cost_center,
                     gl_code, source_document, changed_by, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING tx_id
                """, (item_key, tx_type, quantity, unit, cost, cost_center,
                      gl_code, source_document, changed_by, notes))
                return cur.fetchone()[0]
        except Exception as e:
            print(f"Error logging transaction: {e}")
            return None

    def get_transactions(self, item_key: str = None,
                         tx_type: str = None,
                         cost_center: str = None,
                         limit: int = 200) -> List[Dict]:
        """
        Fetch transactions with optional filters.
        Any filter left as None is ignored (returns all).
        """
        conditions = []
        params = []
        if item_key:
            conditions.append("item_key = %s")
            params.append(item_key)
        if tx_type:
            conditions.append("tx_type = %s")
            params.append(tx_type)
        if cost_center:
            conditions.append("cost_center = %s")
            params.append(cost_center)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT * FROM inventory_transactions {where} "
                f"ORDER BY tx_date DESC LIMIT %s",
                params
            )
            return [dict(r) for r in cur.fetchall()]

    def get_transaction_summary(self, item_key: str) -> Dict:
        """
        Returns net quantity, last receive date, last count date for one item.
        Useful for QOH derivation and display.
        """
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COALESCE(SUM(quantity), 0)                AS net_qty,
                    MAX(CASE WHEN tx_type = 'VENDOR_RECEIPT'
                             THEN tx_date END)                AS last_receive,
                    MAX(CASE WHEN tx_type = 'PAC_COUNT'
                             THEN tx_date END)                AS last_count
                FROM inventory_transactions
                WHERE item_key = %s
            """, (item_key,))
            row = cur.fetchone()
            return {
                "net_qty":      float(row[0]) if row[0] else 0.0,
                "last_receive": row[1],
                "last_count":   row[2],
            }

    # ------------------------------------------------------------------
    # RECIPES (PCA Engine)
    # ------------------------------------------------------------------
    def add_recipe(self, recipe_data: Dict[str, Any]) -> Optional[int]:
        """
        Insert a new recipe. Returns the new recipe_id, or None on failure.
        Required key: 'menu_item'. All other fields optional.
        """
        recipe_data.setdefault("is_active", True)
        recipe_data.setdefault("created_date", datetime.utcnow())
        recipe_data["last_updated"] = datetime.utcnow()
        cols = list(recipe_data.keys())
        vals = list(recipe_data.values())
        placeholders = ", ".join(["%s"] * len(cols))
        col_str = ", ".join(cols)
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO recipes ({col_str}) VALUES ({placeholders}) "
                    f"RETURNING recipe_id",
                    vals
                )
                return cur.fetchone()[0]
        except psycopg2.errors.UniqueViolation:
            return None
        except Exception as e:
            print(f"Error adding recipe: {e}")
            return None

    def upsert_recipe(self, recipe_data: Dict[str, Any]) -> Optional[int]:
        """
        Insert or update a recipe by menu_item (unique key).
        Returns recipe_id.
        """
        menu_item = recipe_data.get("menu_item")
        if not menu_item:
            return None
        existing = self.get_recipe_by_name(menu_item)
        if existing:
            recipe_id = existing["recipe_id"]
            recipe_data["last_updated"] = datetime.utcnow()
            recipe_data.pop("menu_item", None)
            recipe_data.pop("recipe_id", None)
            recipe_data.pop("created_date", None)
            set_clause = ", ".join([f"{k} = %s" for k in recipe_data])
            vals = list(recipe_data.values()) + [recipe_id]
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE recipes SET {set_clause} WHERE recipe_id = %s",
                    vals
                )
            return recipe_id
        else:
            return self.add_recipe(recipe_data)

    def get_recipe(self, recipe_id: int) -> Optional[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM recipes WHERE recipe_id = %s",
                        (recipe_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_recipe_by_name(self, menu_item: str) -> Optional[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM recipes WHERE menu_item = %s",
                        (menu_item,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_recipes(self, active_only: bool = True) -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if active_only:
                cur.execute(
                    "SELECT * FROM recipes WHERE is_active = TRUE "
                    "ORDER BY menu_item"
                )
            else:
                cur.execute("SELECT * FROM recipes ORDER BY menu_item")
            return [dict(r) for r in cur.fetchall()]

    def delete_recipe(self, recipe_id: int,
                      soft: bool = True) -> bool:
        """
        soft=True (default): marks is_active=False, keeps history.
        soft=False: hard delete (cascades to recipe_ingredients).
        """
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                if soft:
                    cur.execute(
                        "UPDATE recipes SET is_active = FALSE, "
                        "last_updated = %s WHERE recipe_id = %s",
                        (datetime.utcnow(), recipe_id)
                    )
                else:
                    cur.execute(
                        "DELETE FROM recipes WHERE recipe_id = %s",
                        (recipe_id,)
                    )
            return True
        except Exception as e:
            print(f"Error deleting recipe {recipe_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # RECIPE INGREDIENTS
    # ------------------------------------------------------------------
    def add_recipe_ingredient(self, recipe_id: int, item_key: str,
                               qty_per_serving: float,
                               unit: str = None,
                               yield_adjusted: bool = True,
                               notes: str = None) -> Optional[int]:
        """
        Add one ingredient line to a recipe.
        Returns ingredient_id, or None on failure.
        """
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO recipe_ingredients
                    (recipe_id, item_key, qty_per_serving, unit,
                     yield_adjusted, notes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING ingredient_id
                """, (recipe_id, item_key, qty_per_serving,
                      unit, yield_adjusted, notes))
                return cur.fetchone()[0]
        except Exception as e:
            print(f"Error adding ingredient: {e}")
            return None

    def get_recipe_ingredients(self, recipe_id: int) -> List[Dict]:
        """
        Returns ingredients joined with current item cost for live costing.
        Applies yield from items table so cost reflects actual usable yield.
        """
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT
                    ri.*,
                    i.description,
                    i.pack_type,
                    i.cost          AS item_cost,
                    i.conv_ratio,
                    COALESCE(i.override_yield, i.yield, 1.0) AS effective_yield,
                    CASE
                        WHEN ri.yield_adjusted
                        THEN ri.qty_per_serving
                             * i.cost
                             / NULLIF(COALESCE(i.override_yield,
                                               i.yield, 1.0), 0)
                        ELSE ri.qty_per_serving * i.cost
                    END             AS ingredient_cost
                FROM recipe_ingredients ri
                JOIN items i ON ri.item_key = i.key
                WHERE ri.recipe_id = %s
                ORDER BY ri.ingredient_id
            """, (recipe_id,))
            return [dict(r) for r in cur.fetchall()]

    def delete_recipe_ingredient(self, ingredient_id: int) -> bool:
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM recipe_ingredients WHERE ingredient_id = %s",
                    (ingredient_id,)
                )
            return True
        except Exception as e:
            print(f"Error deleting ingredient {ingredient_id}: {e}")
            return False

    def get_recipe_cost(self, recipe_id: int) -> Dict:
        """
        Returns total_ingredient_cost and per-ingredient breakdown
        for a recipe at current item prices.
        """
        ingredients = self.get_recipe_ingredients(recipe_id)
        total = sum(
            float(i.get("ingredient_cost") or 0) for i in ingredients
        )
        return {
            "recipe_id":            recipe_id,
            "total_ingredient_cost": round(total, 4),
            "ingredient_count":     len(ingredients),
            "ingredients":          ingredients,
        }

    # ------------------------------------------------------------------
    # HISTORY
    # ------------------------------------------------------------------
    def get_item_history(self, key: str, limit: int = 100) -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM item_history WHERE item_key = %s
                ORDER BY change_date DESC LIMIT %s
            """, (key, limit))
            return [dict(r) for r in cur.fetchall()]

    def get_price_history(self, key: str, limit: int = 50) -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM price_history WHERE item_key = %s
                ORDER BY doc_date DESC LIMIT %s
            """, (key, limit))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # COUNT IMPORT HELPERS  (called by count_importer.commit_count)
    # ------------------------------------------------------------------
    def update_quantity_from_count(self, key: str, new_qty: float,
                                   import_id: str,
                                   changed_by: str = "count_import") -> bool:
        """
        Update quantity_on_hand from a count sheet commit.
        Also writes to item_history for full audit trail.
        Returns True on success, False if item not found or error.
        """
        current = self.get_item(key)
        if not current:
            return False
        old_qty = current.get("quantity_on_hand", 0)
        updates = {
            "quantity_on_hand": new_qty,
            "last_updated":     datetime.utcnow(),
            "status_tag":       "📋 Count Import",
        }
        ok = self._apply_update(key, updates,
                                change_source="count_import",
                                source_document=import_id,
                                changed_by=changed_by)
        if ok:
            # Also log to inventory_transactions
            self.log_transaction(
                item_key=key,
                tx_type="PAC_COUNT",
                quantity=new_qty,
                source_document=import_id,
                changed_by=changed_by,
                notes=f"Previous QOH: {old_qty}"
            )
        return ok

    def log_count_import(self, import_id: str, source_file: str,
                         file_format: str, data_layout: str,
                         count_type: str, count_date: str,
                         cost_center: str, imported_by: str,
                         total_items: int, items_changed: int,
                         items_flagged: int, total_prev_value: float,
                         total_new_value: float,
                         variance_value: float) -> bool:
        """
        Write one row to import_log after a count sheet commit.
        Called by count_importer.commit_count().
        """
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO import_log
                    (import_id, source_file, file_format, data_layout,
                     count_type, count_date, cost_center, imported_by,
                     total_items, items_changed, items_flagged,
                     total_prev_value, total_new_value, variance_value)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (import_id) DO NOTHING
                """, (import_id, source_file, file_format, data_layout,
                      count_type, count_date, cost_center, imported_by,
                      total_items, items_changed, items_flagged,
                      total_prev_value, total_new_value, variance_value))
            return True
        except Exception as e:
            print(f"Error logging count import: {e}")
            return False

    def get_import_log(self, limit: int = 50) -> List[Dict]:
        """Fetch recent count import records, newest first."""
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM import_log
                ORDER BY imported_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # COUNT OVERRIDES  (carried forward from v3.0.2)
    # ------------------------------------------------------------------
    def get_count_overrides_bulk(self) -> Dict[str, float]:
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT item_key, divisor FROM count_overrides"
                )
                return {row[0]: float(row[1]) for row in cur.fetchall()}
        except Exception:
            return {}

    def upsert_count_override(self, item_key: str, divisor: float,
                               cost_center: str = None,
                               notes: str = None) -> bool:
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO count_overrides
                        (item_key, cost_center, divisor, notes)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (item_key, cost_center)
                    DO UPDATE SET divisor = EXCLUDED.divisor,
                                  notes   = EXCLUDED.notes
                """, (item_key, cost_center or "", divisor, notes))
            return True
        except Exception as e:
            print(f"Error upserting count override: {e}")
            return False

    def delete_count_override(self, item_key: str,
                               cost_center: str = None) -> bool:
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM count_overrides "
                    "WHERE item_key = %s AND cost_center = %s",
                    (item_key, cost_center or "")
                )
            return True
        except Exception as e:
            print(f"Error deleting count override: {e}")
            return False

    def get_all_count_overrides(self) -> List[Dict]:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT * FROM count_overrides ORDER BY item_key"
            )
            return [dict(r) for r in cur.fetchall()]

    def get_override_setting(self, key: str,
                              default: str = None) -> Optional[str]:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_value FROM count_override_settings "
                "WHERE setting_key = %s",
                (key,)
            )
            row = cur.fetchone()
            return row[0] if row else default

    def set_override_setting(self, key: str, value: str) -> bool:
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO count_override_settings (setting_key, setting_value)
                    VALUES (%s, %s)
                    ON CONFLICT (setting_key)
                    DO UPDATE SET setting_value = EXCLUDED.setting_value
                """, (key, value))
            return True
        except Exception as e:
            print(f"Error setting override setting: {e}")
            return False

    # ------------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------------
    def _apply_update(self, key: str, updates: Dict[str, Any],
                      change_source: str = "system",
                      source_document: str = None,
                      changed_by: str = "system") -> bool:
        if not updates:
            return True
        current = self.get_item(key)
        if not current:
            return False
        try:
            set_clause = ", ".join([f"{k} = %s" for k in updates])
            vals = list(updates.values()) + [key]
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE items SET {set_clause} WHERE key = %s", vals
                )
            for field, new_val in updates.items():
                if field == "last_updated":
                    continue
                old_val = current.get(field)
                if str(old_val) != str(new_val):
                    self._add_history(
                        key, "field_update",
                        field_changed=field,
                        old_value=str(old_val) if old_val is not None else "",
                        new_value=str(new_val) if new_val is not None else "",
                        change_source=change_source,
                        source_document=source_document,
                        changed_by=changed_by
                    )
            return True
        except Exception as e:
            print(f"Error updating {key}: {e}")
            return False

    def _add_history(self, item_key: str, change_type: str,
                     field_changed: str = None, old_value: str = None,
                     new_value: str = None, change_source: str = None,
                     source_document: str = None, changed_by: str = "system",
                     change_reason: str = None, metadata: Dict = None):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO item_history
                (item_key, change_type, field_changed, old_value, new_value,
                 change_source, source_document, changed_by, change_reason,
                 metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (item_key, change_type, field_changed, old_value, new_value,
                  change_source, source_document, changed_by, change_reason,
                  json.dumps(metadata) if metadata else None))

    def _add_price_history(self, key: str, price: float, doc_date: str,
                           source_file: str = None, vendor: str = None):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO price_history
                (item_key, price, doc_date, source_file, vendor)
                VALUES (%s, %s, %s, %s, %s)
            """, (key, price, doc_date, source_file, vendor))
