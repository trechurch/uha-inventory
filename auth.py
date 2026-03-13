# ──────────────────────────────────────────────────────────────────────────────
#  auth.py  —  User Authentication + Identity Resolution
#  Tiered detection order:
#    1. Streamlit Cloud OAuth  (st.user.email)
#    2. Windows OS user        (os.environ['USERNAME'])
#    3. Linux / Mac OS user    (os.environ['USER'] / LOGNAME)
#    4. Login form fallback    (username + PIN stored in DB)
#
#  Resolved identity stored in st.session_state["current_user"]:
#    {
#      username:     str,   # short unique key  (e.g. "tre_church")
#      display_name: str,   # human label       (e.g. "Tre' Church")
#      email:        str,   # email if available
#      role:         str,   # "viewer" | "editor" | "admin"
#      auth_method:  str,   # "oauth" | "os" | "login_form"
#    }
#
#  History fields populated:
#    changed_by      → get_changed_by()   "username (Display Name)"
#    change_reason   → why_prompt()       user-entered text
# ──────────────────────────────────────────────────────────────────────────────

import os
import hashlib
from typing import Optional, Dict

import streamlit as st

# ── end of imports ────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
#  VERSION
# ──────────────────────────────────────────────────────────────────────────────

__version__ = "1.0.0"

# ── end of version ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

#  OS accounts that belong to services / daemons, not real people.
#  If the resolved OS username matches any of these (case-insensitive),
#  the OS tier is skipped and we fall through to the login form.
_SERVICE_ACCOUNTS = {
    'system', 'administrator', 'root', 'service',
    'network service', 'local service', 'nobody',
    'daemon', 'www-data', 'postgres', 'streamlit',
    'runner', 'github-actions', 'circleci',
}

ROLES = ('viewer', 'editor', 'admin')

# ── end of constants ──────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PIN HASHING
#  Uses PBKDF2-HMAC-SHA256 with the username as salt.
#  stdlib only — no bcrypt dependency required.
# ──────────────────────────────────────────────────────────────────────────────

def _hash_pin(pin: str, username: str) -> str:
    """Return PBKDF2-HMAC-SHA256 hex digest of pin, salted by username."""
    key = hashlib.pbkdf2_hmac(
        'sha256',
        pin.encode('utf-8'),
        username.strip().lower().encode('utf-8'),
        iterations=100_000,
    )
    return key.hex()


def verify_pin(pin: str, username: str, stored_hash: str) -> bool:
    """Constant-time comparison of supplied PIN against stored hash."""
    return _hash_pin(pin, username) == stored_hash

# ── end of pin hashing ────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  TIER 1 — STREAMLIT CLOUD OAUTH
# ──────────────────────────────────────────────────────────────────────────────

def _try_oauth() -> Optional[Dict]:
    """
    Attempt Streamlit Cloud OAuth identity resolution.
    Returns user dict on success, None if OAuth is not configured or the
    session has no authenticated email.
    """
    try:
        # st.user is available in Streamlit ≥ 1.28.
        # st.experimental_user is the older alias — try both.
        user_obj = getattr(st, 'user', None) or getattr(st, 'experimental_user', None)
        if user_obj is None:
            return None
        email = getattr(user_obj, 'email', None)
        if not email or '@' not in str(email):
            return None
        email        = str(email).strip()
        display_name = str(getattr(user_obj, 'name', '') or email.split('@')[0])
        username     = email.split('@')[0].lower().replace('.', '_')
        return {
            'username':     username,
            'display_name': display_name,
            'email':        email,
            'role':         'editor',
            'auth_method':  'oauth',
        }
    except Exception:
        return None

# ── end of tier 1 ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  TIER 2 — OS USERNAME
#  Works reliably on local Windows deployments.  On Streamlit Cloud the
#  env var returns the server's system account, which is filtered out by
#  _SERVICE_ACCOUNTS.
# ──────────────────────────────────────────────────────────────────────────────

def _try_os_user() -> Optional[Dict]:
    """
    Attempt OS-level username detection.
    Windows: os.environ['USERNAME']
    Linux / Mac: os.environ['USER'] or os.environ['LOGNAME']
    Returns user dict on success, None if no valid user found.
    """
    username = (
        os.environ.get('USERNAME') or   # Windows — most reliable
        os.environ.get('USER')     or   # POSIX
        os.environ.get('LOGNAME')        # POSIX fallback
    )
    if not username:
        return None
    username = username.strip()
    if not username or len(username) < 2:
        return None
    if username.lower() in _SERVICE_ACCOUNTS:
        return None

    # Clean up display name — Windows usernames can be "DOMAIN\user"
    display = username.split('\\')[-1]

    return {
        'username':     username.lower().replace('\\', '_').replace(' ', '_'),
        'display_name': display,
        'email':        '',
        'role':         'editor',
        'auth_method':  'os',
    }

# ── end of tier 2 ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  TIER 3 — LOGIN FORM
# ──────────────────────────────────────────────────────────────────────────────

def _render_login_form(db) -> Optional[Dict]:
    """
    Show a username + PIN login form.
    • First-time users: account is created automatically on first sign-in.
    • Existing users: PIN is verified against the stored hash.
    • Users with no PIN set: the supplied PIN becomes their PIN.
    Returns a user dict on success, None if the form hasn't been submitted
    or credentials are invalid.
    """
    st.markdown("---")
    st.subheader("🔐 Sign In")
    st.caption("Enter your username and PIN to continue.")

    col1, col2 = st.columns([2, 1])
    with col1:
        username_input = st.text_input(
            "Username",
            placeholder="e.g. tre_church",
            key="_login_username",
        )
    with col2:
        pin_input = st.text_input(
            "PIN",
            placeholder="••••••",
            type="password",
            key="_login_pin",
        )

    submit = st.button("Sign In", type="primary", key="_login_submit",
                       use_container_width=False)

    if not submit:
        st.caption(
            "First sign-in? Enter any username and PIN — your account will be "
            "created automatically."
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

    # ── DB lookup ─────────────────────────────────────────────────────────────
    try:
        db_user = db.get_user_by_username(username)
    except Exception as e:
        st.error(f"Database error: {e}")
        return None

    # ── New user — create account ─────────────────────────────────────────────
    if db_user is None:
        new_user = {
            'username':     username,
            'display_name': username,
            'email':        '',
            'pin_hash':     _hash_pin(pin, username),
            'role':         'editor',
            'auth_method':  'login_form',
        }
        try:
            db.upsert_user(new_user)
            db.update_last_login(username)
        except Exception as e:
            st.error(f"Could not create account: {e}")
            return None
        st.success(f"✅ Account created for **{username}**. Welcome!")
        return {**new_user, 'auth_method': 'login_form'}

    # ── Existing user — no PIN set yet → set it now ───────────────────────────
    if not db_user.get('pin_hash'):
        db.upsert_user({'username': username, 'pin_hash': _hash_pin(pin, username)})
        db.update_last_login(username)
        return _user_from_db_row(db_user, 'login_form')

    # ── Existing user — verify PIN ────────────────────────────────────────────
    if not verify_pin(pin, username, db_user['pin_hash']):
        st.error("Incorrect PIN.")
        return None

    if not db_user.get('is_active', True):
        st.error("Your account has been deactivated. Contact an administrator.")
        return None

    db.update_last_login(username)
    return _user_from_db_row(db_user, 'login_form')

# ── end of tier 3 ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _user_from_db_row(row: Dict, auth_method: str) -> Dict:
    """Build a session user dict from a DB users row."""
    return {
        'username':     row.get('username', ''),
        'display_name': row.get('display_name') or row.get('username', ''),
        'email':        row.get('email', ''),
        'role':         row.get('role', 'editor'),
        'auth_method':  auth_method,
    }


def _finalize_user(user: Dict, db) -> None:
    """
    Upsert OAuth / OS users into the DB so they get a persistent record
    (and so an admin can later promote them or change their role).
    Then store the resolved user in session state.
    """
    if user.get('auth_method') != 'login_form':
        try:
            db.upsert_user({
                'username':     user['username'],
                'display_name': user['display_name'],
                'email':        user.get('email', ''),
                'auth_method':  user['auth_method'],
            })
            # Pull role from DB — an admin may have promoted this user
            db_row = db.get_user_by_username(user['username'])
            if db_row:
                user['role'] = db_row.get('role', user.get('role', 'editor'))
            db.update_last_login(user['username'])
        except Exception:
            pass   # non-fatal — identity still valid from OS / OAuth

    st.session_state['current_user'] = user

# ── end of internal helpers ───────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — REQUIRE AUTH
#  Call once at the very top of main(), before any page is rendered.
#  Returns True if identity is established (rendering continues normally).
#  Returns False if the login form was shown (rendering should stop).
# ──────────────────────────────────────────────────────────────────────────────

def require_auth(db) -> bool:
    """
    Resolve user identity via tiered detection.
    Idempotent — already-authenticated sessions return True immediately.
    """
    # Already authenticated this session
    if st.session_state.get('current_user'):
        return True

    # Tier 1: Streamlit Cloud OAuth
    user = _try_oauth()
    if user:
        _finalize_user(user, db)
        return True

    # Tier 2: OS username (local deployment)
    user = _try_os_user()
    if user:
        _finalize_user(user, db)
        return True

    # Tier 3: Login form — renders the form and returns False until signed in
    user = _render_login_form(db)
    if user:
        _finalize_user(user, db)
        st.rerun()   # re-render so the page loads with identity set
    return False

# ── end of require auth ───────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — ACCESSORS
# ──────────────────────────────────────────────────────────────────────────────

def get_current_user() -> Dict:
    """Return the current user dict. Falls back to anonymous if not set."""
    return st.session_state.get('current_user') or {
        'username':     'anonymous',
        'display_name': 'Anonymous',
        'email':        '',
        'role':         'viewer',
        'auth_method':  'none',
    }


def get_changed_by() -> str:
    """
    Canonical changed_by string for item_history records.
    Format: "username (Display Name)" or just "username" if they match.
    """
    u    = get_current_user()
    user = u.get('username', 'unknown')
    name = u.get('display_name', '')
    if name and name.lower() != user.lower():
        return f"{user} ({name})"
    return user


def get_role() -> str:
    return get_current_user().get('role', 'viewer')


def is_admin() -> bool:
    return get_role() == 'admin'


def is_editor() -> bool:
    return get_role() in ('editor', 'admin')


def can_view() -> bool:
    return True   # any authenticated user can view

# ── end of accessors ──────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — WHY PROMPT
#  Render a reason text input before a significant action.
#  Returns the entered string (stripped), or None if empty / not yet entered.
#  When required=True the action button in the caller should be disabled
#  until this returns a non-None value.
# ──────────────────────────────────────────────────────────────────────────────

def why_prompt(
    label:    str  = "Reason for change",
    required: bool = False,
    key:      str  = "why_reason",
    placeholder: str = "",
) -> Optional[str]:
    """
    Small reason input rendered inline before a write action.

    Usage:
        reason = why_prompt("Why are you editing this item?", required=True, key="edit_reason")
        submit = st.button("Save", disabled=(reason is None))
        if submit:
            db._apply_update(key, updates, change_reason=reason, ...)
    """
    ph = placeholder or (
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

# ── end of why prompt ─────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — SIGN OUT
# ──────────────────────────────────────────────────────────────────────────────

def sign_out() -> None:
    """Clear current user from session state and trigger a rerun."""
    st.session_state.pop('current_user', None)

# ── end of sign out ───────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — USER BADGE  (sidebar widget)
# ──────────────────────────────────────────────────────────────────────────────

def render_user_badge() -> None:
    """
    Compact identity widget for the sidebar.
    Shows name, role, auth method, and a Sign Out button.
    """
    u = get_current_user()
    method_icons = {
        'oauth':      '🔵',
        'os':         '🖥️',
        'login_form': '🔑',
        'none':       '👤',
    }
    icon = method_icons.get(u.get('auth_method', 'none'), '👤')
    st.markdown(f"**{icon} {u.get('display_name', 'Unknown')}**")
    role        = u.get('role', 'viewer')
    auth_method = u.get('auth_method', '?')
    email       = u.get('email', '')
    detail      = email if email else auth_method
    st.caption(f"`{role}` · {detail}")
    if st.button("Sign Out", key="auth_signout_btn", use_container_width=True):
        sign_out()
        st.rerun()

# ── end of user badge ─────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — USER MANAGEMENT PAGE  (called from page_settings or standalone)
# ──────────────────────────────────────────────────────────────────────────────

def render_user_management(db) -> None:
    """
    Admin-only user management panel.
    Lists all users, allows role changes, PIN resets, and deactivation.
    """
    if not is_admin():
        st.warning("🔒 Admin access required.")
        return

    st.subheader("👥 User Management")
    st.caption("Admin only. Manage roles, reset PINs, and deactivate accounts.")

    try:
        users = db.get_all_users()
    except Exception as e:
        st.error(f"Could not load users: {e}")
        return

    if not users:
        st.info("No users registered yet.")
        return

    for u in users:
        uname = u.get('username', '?')
        with st.expander(
            f"**{u.get('display_name') or uname}** `{uname}` · "
            f"`{u.get('role', 'editor')}` · "
            f"{'✅ active' if u.get('is_active', True) else '❌ inactive'}",
            expanded=False,
        ):
            col1, col2, col3 = st.columns(3)

            # Role selector
            with col1:
                new_role = st.selectbox(
                    "Role",
                    ROLES,
                    index=list(ROLES).index(u.get('role', 'editor'))
                          if u.get('role', 'editor') in ROLES else 1,
                    key=f"role_{uname}",
                )
                if st.button("Update Role", key=f"role_btn_{uname}"):
                    db.upsert_user({'username': uname, 'role': new_role})
                    st.success(f"Role updated → {new_role}")
                    st.rerun()

            # PIN reset
            with col2:
                new_pin = st.text_input(
                    "New PIN", type="password",
                    placeholder="leave blank to keep current",
                    key=f"pin_{uname}",
                )
                if st.button("Reset PIN", key=f"pin_btn_{uname}"):
                    if new_pin and len(new_pin) >= 4:
                        db.upsert_user({
                            'username': uname,
                            'pin_hash': _hash_pin(new_pin.strip(), uname),
                        })
                        st.success("PIN reset.")
                    else:
                        st.warning("PIN must be at least 4 characters.")

            # Activate / deactivate
            with col3:
                is_active = u.get('is_active', True)
                label     = "Deactivate" if is_active else "Reactivate"
                if st.button(label, key=f"active_btn_{uname}", type="secondary"):
                    db.upsert_user({'username': uname, 'is_active': not is_active})
                    st.success(f"Account {'deactivated' if is_active else 'reactivated'}.")
                    st.rerun()

            # Meta info
            last = u.get('last_login') or 'Never'
            st.caption(
                f"Email: {u.get('email') or '—'} · "
                f"Method: {u.get('auth_method') or '—'} · "
                f"Last login: {str(last)[:19]}"
            )

# ── end of user management page ───────────────────────────────────────────────
