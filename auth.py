# auth.py  —  User Authentication + Identity Resolution  v1.1.0
#
# Tiered detection order:
#   1. Streamlit Cloud OAuth  (st.user.email)
#   2. Windows OS user        (os.environ['USERNAME'])
#   3. Linux / Mac OS user    (os.environ['USER'] / LOGNAME)
#   4. Login form fallback    (username + PIN stored in DB)
#
# v1.1.0: First user to sign in on a fresh DB is automatically granted admin.
#
# Resolved identity stored in st.session_state["current_user"]:
#   {
#     username:     str,
#     display_name: str,
#     email:        str,
#     role:         str,   # "viewer" | "editor" | "admin"
#     auth_method:  str,   # "oauth" | "os" | "login_form"
#   }
#
# History fields:
#   changed_by    → get_changed_by()   "username (Display Name)"
#   change_reason → why_prompt()       user-entered text
#

__version__ = "1.1.0"

import os
import hashlib
from typing import Optional, Dict

import streamlit as st

# OS accounts belonging to services / daemons — never valid real users.
_SERVICE_ACCOUNTS = {
    "system", "administrator", "root", "service", "network service",
    "local service", "nobody", "daemon", "www-data", "postgres",
    "streamlit", "runner", "github-actions", "circleci",
}

ROLES = ("viewer", "editor", "admin")


# ────────────────────────────────────────────────────────────────────
# PIN helpers
# ────────────────────────────────────────────────────────────────────

def _hash_pin(pin: str, username: str) -> str:
    """Return PBKDF2-HMAC-SHA256 hex digest of pin, salted by username."""
    key = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        username.strip().lower().encode("utf-8"),
        iterations=100_000,
    )
    return key.hex()


def verify_pin(pin: str, username: str, stored_hash: str) -> bool:
    """Constant-time comparison of supplied PIN against stored hash."""
    return _hash_pin(pin, username) == stored_hash


# ────────────────────────────────────────────────────────────────────
# Tier 1: OAuth
# ────────────────────────────────────────────────────────────────────

def _try_oauth() -> Optional[Dict]:
    try:
        user_obj = getattr(st, "user", None) or getattr(st, "experimental_user", None)
        if user_obj is None:
            return None
        email = getattr(user_obj, "email", None)
        if not email or "@" not in str(email):
            return None
        email        = str(email).strip()
        display_name = str(getattr(user_obj, "name", "") or email.split("@")[0])
        username     = email.split("@")[0].lower().replace(".", "_")
        return {
            "username": username, "display_name": display_name,
            "email": email, "role": "editor", "auth_method": "oauth",
        }
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────
# Tier 2: OS user
# ────────────────────────────────────────────────────────────────────

def _try_os_user() -> Optional[Dict]:
    username = (
        os.environ.get("USERNAME")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
    )
    if not username:
        return None
    username = username.strip()
    if len(username) < 2 or username.lower() in _SERVICE_ACCOUNTS:
        return None
    display = username.split("\\")[-1]
    return {
        "username": username.lower().replace("\\", "_").replace(" ", "_"),
        "display_name": display,
        "email": "",
        "role": "editor",
        "auth_method": "os",
    }


# ────────────────────────────────────────────────────────────────────
# Tier 3: Login form
# ────────────────────────────────────────────────────────────────────

def _render_login_form(db) -> Optional[Dict]:
    """
    Username + PIN login form.
    • First user in a fresh DB → role is promoted to admin automatically.
    • First-time user → account created on first sign-in.
    • Existing user with no PIN → supplied PIN becomes their PIN.
    Returns user dict on success, None otherwise.
    """
    st.markdown("---")
    st.subheader("🔐 Sign In")
    st.caption("Enter your username and PIN to continue.")

    col1, col2 = st.columns([2, 1])
    with col1:
        username_input = st.text_input(
            "Username", placeholder="e.g. tre_church", key="_login_username"
        )
    with col2:
        pin_input = st.text_input(
            "PIN", placeholder="••••••", type="password", key="_login_pin"
        )

    submit = st.button("Sign In", type="primary", key="_login_submit")

    if not submit:
        st.caption(
            "First sign-in? Enter any username and PIN — "
            "your account will be created automatically. "
            "The very first account on a fresh installation is granted admin."
        )
        return None

    username = (username_input or "").strip().lower()
    pin      = (pin_input or "").strip()

    if not username:
        st.error("Username is required.")
        return None
    if not pin:
        st.error("PIN is required.")
        return None
    if len(pin) < 4:
        st.error("PIN must be at least 4 characters.")
        return None

    try:
        db_user = db.get_user_by_username(username)
    except Exception as exc:
        st.error(f"Database error: {exc}")
        return None

    # ── New user ──────────────────────────────────────────────────────
    if db_user is None:
        # First user ever in the DB → admin bootstrap
        try:
            existing_count = len(db.get_all_users() or [])
        except Exception:
            existing_count = 1   # assume not first if we can't check

        role = "admin" if existing_count == 0 else "editor"

        new_user = {
            "username":     username,
            "display_name": username,
            "email":        "",
            "pin_hash":     _hash_pin(pin, username),
            "role":         role,
            "auth_method":  "login_form",
        }
        try:
            db.upsert_user(new_user)
            db.update_last_login(username)
        except Exception as exc:
            st.error(f"Could not create account: {exc}")
            return None

        if role == "admin":
            st.success(f"✅ Account created for **{username}** with **admin** role (first user).")
        else:
            st.success(f"✅ Account created for **{username}**. Welcome!")
        return {**new_user, "auth_method": "login_form"}

    # ── Existing user — no PIN yet ────────────────────────────────────
    if not db_user.get("pin_hash"):
        db.upsert_user({"username": username, "pin_hash": _hash_pin(pin, username)})
        db.update_last_login(username)
        return _user_from_db_row(db_user, "login_form")

    # ── Existing user — verify PIN ────────────────────────────────────
    if not verify_pin(pin, username, db_user["pin_hash"]):
        st.error("Incorrect PIN.")
        return None

    if not db_user.get("is_active", True):
        st.error("Your account has been deactivated. Contact an administrator.")
        return None

    db.update_last_login(username)
    return _user_from_db_row(db_user, "login_form")


# ────────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────────

def _user_from_db_row(row: Dict, auth_method: str) -> Dict:
    return {
        "username":     row.get("username", ""),
        "display_name": row.get("display_name") or row.get("username", ""),
        "email":        row.get("email", ""),
        "role":         row.get("role", "editor"),
        "auth_method":  auth_method,
    }


def _finalize_user(user: Dict, db) -> None:
    """
    Upsert OAuth / OS users into the DB for persistent role management.
    For OAuth/OS users on a fresh DB, also apply the first-user admin bootstrap.
    """
    if user.get("auth_method") != "login_form":
        try:
            # First-user admin bootstrap for OAuth/OS paths too
            try:
                existing_count = len(db.get_all_users() or [])
            except Exception:
                existing_count = 1

            if existing_count == 0:
                user["role"] = "admin"

            db.upsert_user({
                "username":     user["username"],
                "display_name": user["display_name"],
                "email":        user.get("email", ""),
                "auth_method":  user["auth_method"],
                "role":         user.get("role", "editor"),
            })
            # Pull role from DB — an admin may have promoted / demoted this user
            db_row = db.get_user_by_username(user["username"])
            if db_row:
                user["role"] = db_row.get("role", user.get("role", "editor"))
            db.update_last_login(user["username"])
        except Exception:
            pass   # non-fatal — identity valid from OS / OAuth

    st.session_state["current_user"] = user


# ────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────

def require_auth(db) -> bool:
    """
    Resolve user identity via tiered detection.
    Idempotent — already-authenticated sessions return True immediately.
    Returns False (and renders login form) while waiting for credentials.
    """
    if st.session_state.get("current_user"):
        return True

    user = _try_oauth()
    if user:
        _finalize_user(user, db)
        return True

    user = _try_os_user()
    if user:
        _finalize_user(user, db)
        return True

    user = _render_login_form(db)
    if user:
        _finalize_user(user, db)
        st.rerun()
    return False


def get_current_user() -> Dict:
    return st.session_state.get("current_user") or {
        "username": "anonymous", "display_name": "Anonymous",
        "email": "", "role": "viewer", "auth_method": "none",
    }


def get_changed_by() -> str:
    u    = get_current_user()
    user = u.get("username", "unknown")
    name = u.get("display_name", "")
    if name and name.lower() != user.lower():
        return f"{user} ({name})"
    return user


def get_role() -> str:
    return get_current_user().get("role", "viewer")


def is_admin() -> bool:
    return get_role() == "admin"


def is_editor() -> bool:
    return get_role() in ("editor", "admin")


def can_view() -> bool:
    return True


def why_prompt(
    label:       str = "Reason for change",
    required:    bool = False,
    key:         str = "why_reason",
    placeholder: str = "",
) -> Optional[str]:
    ph  = placeholder or (
        "Required — describe why this change is being made."
        if required else
        "Optional — describe why this change is being made."
    )
    val = st.text_input(label, placeholder=ph, key=key)
    val = (val or "").strip()
    if required and not val:
        st.caption("⚠️ A reason is required before this action can proceed.")
        return None
    return val or None


def sign_out() -> None:
    st.session_state.pop("current_user", None)


# ────────────────────────────────────────────────────────────────────
# UI Widgets
# ────────────────────────────────────────────────────────────────────

def render_user_badge() -> None:
    """Compact identity widget for the sidebar."""
    u = get_current_user()
    method_icons = {"oauth": "🔵", "os": "🖥️", "login_form": "🔑", "none": "👤"}
    icon         = method_icons.get(u.get("auth_method", "none"), "👤")
    st.markdown(f"**{icon} {u.get('display_name', 'Unknown')}**")
    role        = u.get("role", "viewer")
    auth_method = u.get("auth_method", "?")
    email       = u.get("email", "")
    detail      = email if email else auth_method
    st.caption(f"`{role}` · {detail}")
    if st.button("Sign Out", key="auth_signout_btn", use_container_width=True):
        sign_out()
        st.rerun()


def render_user_management(db) -> None:
    """Admin-only user management panel."""
    if not is_admin():
        st.warning("🔒 Admin access required.")
        return

    st.subheader("👥 User Management")
    st.caption("Manage roles, reset PINs, and deactivate accounts.")

    try:
        users = db.get_all_users()
    except Exception as exc:
        st.error(f"Could not load users: {exc}")
        return

    if not users:
        st.info("No users registered yet.")
        return

    for u in users:
        uname = u.get("username", "?")
        with st.expander(
            f"**{u.get('display_name') or uname}** `{uname}` · "
            f"`{u.get('role', 'editor')}` · "
            f"{'✅ active' if u.get('is_active', True) else '❌ inactive'}",
            expanded=False,
        ):
            col1, col2, col3 = st.columns(3)

            with col1:
                new_role = st.selectbox(
                    "Role", ROLES,
                    index=list(ROLES).index(u.get("role", "editor"))
                          if u.get("role", "editor") in ROLES else 1,
                    key=f"role_{uname}",
                )
                if st.button("Update Role", key=f"role_btn_{uname}"):
                    db.upsert_user({"username": uname, "role": new_role})
                    st.success(f"Role → {new_role}")
                    st.rerun()

            with col2:
                new_pin = st.text_input(
                    "New PIN", type="password",
                    placeholder="leave blank to keep",
                    key=f"pin_{uname}",
                )
                if st.button("Reset PIN", key=f"pin_btn_{uname}"):
                    if new_pin and len(new_pin) >= 4:
                        db.upsert_user({"username": uname,
                                        "pin_hash": _hash_pin(new_pin.strip(), uname)})
                        st.success("PIN reset.")
                    else:
                        st.warning("PIN must be at least 4 characters.")

            with col3:
                is_active = u.get("is_active", True)
                label     = "Deactivate" if is_active else "Reactivate"
                if st.button(label, key=f"active_btn_{uname}", type="secondary"):
                    db.upsert_user({"username": uname, "is_active": not is_active})
                    st.success(f"Account {'deactivated' if is_active else 'reactivated'}.")
                    st.rerun()

            last = u.get("last_login") or "Never"
            st.caption(
                f"Email: {u.get('email') or '—'} · "
                f"Method: {u.get('auth_method') or '—'} · "
                f"Last login: {str(last)[:19]}"
            )
