"""
Inventory Database - PostgreSQL/Supabase version
Drop-in replacement for SQLite database.py
Connection string loaded from environment / Streamlit secrets
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

    # ------------------------------------------------------------------
    # SCHEMA
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

                CREATE INDEX IF NOT EXISTS idx_items_description ON items(description);
                CREATE INDEX IF NOT EXISTS idx_items_gl_code     ON items(gl_code);
                CREATE INDEX IF NOT EXISTS idx_items_vendor      ON items(vendor);
                CREATE INDEX IF NOT EXISTS idx_history_item_key  ON item_history(item_key);
            """)

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
    # CRUD
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

        if incoming.get("cost"):
            updates["cost"] = incoming["cost"]
            updates["status_tag"] = "✅ Updated Today"
        if "quantity_on_hand" in incoming:
            updates["quantity_on_hand"] = incoming["quantity_on_hand"]
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
                cur.execute(f"UPDATE items SET {set_clause} WHERE key = %s", vals)
            for field, new_val in updates.items():
                if field == "last_updated":
                    continue
                old_val = current.get(field)
                if str(old_val) != str(new_val):
                    self._add_history(key, "field_update",
                                      field_changed=field,
                                      old_value=str(old_val) if old_val is not None else "",
                                      new_value=str(new_val) if new_val is not None else "",
                                      change_source=change_source,
                                      source_document=source_document,
                                      changed_by=changed_by)
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
                 change_source, source_document, changed_by, change_reason, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (item_key, change_type, field_changed, old_value, new_value,
                  change_source, source_document, changed_by, change_reason,
                  json.dumps(metadata) if metadata else None))

    def _add_price_history(self, key: str, price: float, doc_date: str,
                           source_file: str = None, vendor: str = None):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO price_history (item_key, price, doc_date, source_file, vendor)
                VALUES (%s, %s, %s, %s, %s)
            """, (key, price, doc_date, source_file, vendor))
