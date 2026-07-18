"""Local human identity, service credentials, sessions, and authorization."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
try:
    from argon2.exceptions import InvalidHashError
except ImportError:  # argon2-cffi 21.x
    from argon2.exceptions import InvalidHash as InvalidHashError


SESSION_COOKIE = "hive_session"
SESSION_HOURS = 12
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{2,39}$")
API_KEY_PATTERN = re.compile(r"^hive_[A-Za-z0-9_-]{32,}$")
BOOTSTRAP_PATH = Path(__file__).parent.parent / "data" / "hive-bootstrap.token"
PASSWORD_HASHER = PasswordHasher(
    time_cost=2, memory_cost=19_456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID,
)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("HIVE timing equalization value only")

PERMISSIONS = {
    "view", "operate", "supervise", "plan", "maintain", "quality", "procure",
    "alerts", "optimize", "commission", "admin", "integration",
}
SERVICE_KEY_PERMISSIONS = {"integration"}
ROLE_PERMISSIONS = {
    "admin": set(PERMISSIONS),
    "supervisor": {"view", "operate", "supervise", "plan", "alerts", "optimize", "quality"},
    "planner": {"view", "plan", "procure", "optimize"},
    "maintenance": {"view", "maintain", "alerts"},
    "quality": {"view", "quality", "alerts"},
    "operator": {"view", "operate", "alerts"},
    "viewer": {"view"},
}
ROLES = tuple(ROLE_PERMISSIONS)

COMMON_PASSWORDS = {
    "password", "password123", "123456789012345", "qwertyuiop12345",
    "adminadminadmin", "letmeinletmein", "hiveoshiveoshive", "haeevhaeevhaeev",
}
PUBLIC_PATHS = {
    "/health", "/auth/status", "/auth/login", "/auth/bootstrap",
}
SELF_AUTH_PATHS = {"/auth/me", "/auth/logout", "/auth/password"}
ADMIN_AUTH_PREFIXES = ("/auth/users", "/auth/api-keys", "/auth/events", "/mqtt-security")
ACTOR_FIELDS = ("actor", "completed_by", "inspector", "operator")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_username(value: str) -> str:
    username = (value or "").strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Username must be 3-40 lowercase letters, numbers, dots, dashes, or underscores")
    return username


def _named(value: str, label: str = "Display name") -> str:
    result = " ".join((value or "").split())
    if len(result) < 2 or len(result) > 120:
        raise ValueError(f"{label} must be 2-120 characters")
    return result


def _validate_password(password: str, username: str = "", display_name: str = "") -> None:
    if len(password) < 15:
        raise ValueError("Password must contain at least 15 characters")
    if len(password) > 128:
        raise ValueError("Password must contain at most 128 characters")
    lowered = password.casefold()
    terms = {username.casefold(), *display_name.casefold().split()}
    if lowered in COMMON_PASSWORDS or any(len(term) >= 4 and term in lowered for term in terms):
        raise ValueError("Choose a password that does not contain common words or account details")


def _event(conn: sqlite3.Connection, event_type: str, actor_name: str,
           success: bool, *, actor_user_id: Optional[int] = None,
           target_type: Optional[str] = None, target_key: Optional[str] = None,
           client_ip: Optional[str] = None, user_agent: Optional[str] = None,
           details: Optional[dict] = None, now: Optional[datetime] = None) -> None:
    conn.execute(
        """INSERT INTO auth_events
           (event_type,actor_user_id,actor_name,target_type,target_key,success,
            client_ip,user_agent,details_json,ts) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (event_type, actor_user_id, actor_name, target_type, target_key, int(success),
         client_ip, (user_agent or "")[:500] or None,
         json.dumps(details or {}, sort_keys=True), _iso(now or _now())),
    )


def auth_required() -> bool:
    return os.getenv("HIVE_AUTH_MODE", "required").strip().lower() != "disabled"


def setup_required(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM auth_users WHERE active=1").fetchone()[0] == 0


def ensure_bootstrap_token(conn: sqlite3.Connection, path: Path = BOOTSTRAP_PATH) -> Optional[Path]:
    if not setup_required(conn):
        if path.exists():
            path.unlink()
        return None
    configured = os.getenv("HIVE_BOOTSTRAP_TOKEN", "").strip()
    if configured:
        return None
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32), encoding="ascii")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path


def _bootstrap_secret(path: Path = BOOTSTRAP_PATH) -> str:
    configured = os.getenv("HIVE_BOOTSTRAP_TOKEN", "").strip()
    if configured:
        return configured
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise ValueError("Bootstrap token is unavailable; regenerate it locally on the central PC") from error


def status(conn: sqlite3.Connection, *, transport_acceptable: bool) -> dict:
    required = auth_required()
    setup = setup_required(conn) if required else False
    return {
        "auth_required": required,
        "setup_required": setup,
        "transport_acceptable": transport_acceptable,
        "bootstrap_token_path": str(BOOTSTRAP_PATH) if setup and not os.getenv("HIVE_BOOTSTRAP_TOKEN") else None,
        "roles": [{"key": role, "permissions": sorted(permissions)}
                  for role, permissions in ROLE_PERMISSIONS.items()],
        "password_policy": {"minimum_length": 15, "maximum_length": 128, "composition_rules": False},
    }


def _public_user(row: dict) -> dict:
    return {
        "id": row["id"], "username": row["username"], "display_name": row["display_name"],
        "role": row["role"], "permissions": sorted(ROLE_PERMISSIONS.get(row["role"], set())),
        "active": bool(row["active"]), "last_login_at": row.get("last_login_at"),
        "password_changed_at": row.get("password_changed_at"), "created_at": row.get("created_at"),
        "version": row.get("version"),
    }


def _new_session(conn: sqlite3.Connection, user: dict, client_ip: Optional[str],
                 user_agent: Optional[str], now: datetime) -> dict:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=SESSION_HOURS)
    cursor = conn.execute(
        """INSERT INTO auth_sessions
           (user_id,token_hash,csrf_token,created_at,expires_at,client_ip,user_agent)
           VALUES (?,?,?,?,?,?,?)""",
        (user["id"], _token_hash(token), csrf, _iso(now), _iso(expires), client_ip,
         (user_agent or "")[:500] or None),
    )
    return {"session_id": cursor.lastrowid, "token": token, "csrf_token": csrf,
            "expires_at": _iso(expires), "user": _public_user(user)}


def bootstrap(conn: sqlite3.Connection, payload: dict, *, client_ip: Optional[str] = None,
              user_agent: Optional[str] = None, now: Optional[datetime] = None,
              token_path: Path = BOOTSTRAP_PATH) -> dict:
    now = now or _now()
    if not setup_required(conn):
        raise ValueError("HIVE access control has already been initialized")
    supplied = (payload.get("bootstrap_token") or "").strip()
    if not supplied or not hmac.compare_digest(supplied, _bootstrap_secret(token_path)):
        _event(conn, "bootstrap_failed", "bootstrap", False, client_ip=client_ip,
               user_agent=user_agent, details={"reason": "invalid_token"}, now=now)
        conn.commit()
        raise ValueError("Invalid bootstrap token")
    username = _normalize_username(payload.get("username"))
    display_name = _named(payload.get("display_name"))
    password = payload.get("password") or ""
    _validate_password(password, username, display_name)
    password_hash = PASSWORD_HASHER.hash(password)
    cursor = conn.execute(
        """INSERT INTO auth_users
           (username,display_name,password_hash,role,active,password_changed_at,created_by,created_at,updated_at)
           VALUES (?,?,?,'admin',1,?,'bootstrap',?,?)""",
        (username, display_name, password_hash, _iso(now), _iso(now), _iso(now)),
    )
    user = dict(conn.execute("SELECT * FROM auth_users WHERE id=?", (cursor.lastrowid,)).fetchone())
    session = _new_session(conn, user, client_ip, user_agent, now)
    _event(conn, "bootstrap_completed", display_name, True, actor_user_id=user["id"],
           target_type="user", target_key=username, client_ip=client_ip,
           user_agent=user_agent, now=now)
    conn.commit()
    if token_path.exists():
        token_path.unlink()
    return session


def login(conn: sqlite3.Connection, payload: dict, *, client_ip: Optional[str] = None,
          user_agent: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    candidate = (payload.get("username") or "").strip().lower()
    row = conn.execute("SELECT * FROM auth_users WHERE username=? COLLATE NOCASE", (candidate,)).fetchone()
    user = dict(row) if row else None
    valid = False
    locked = bool(user and user["locked_until"] and _parse(user["locked_until"]) > now)
    if user and user["active"] and not locked:
        try:
            valid = PASSWORD_HASHER.verify(user["password_hash"], payload.get("password") or "")
        except (VerifyMismatchError, InvalidHashError):
            valid = False
    else:
        try:
            PASSWORD_HASHER.verify(DUMMY_PASSWORD_HASH, payload.get("password") or "")
        except (VerifyMismatchError, InvalidHashError):
            pass
    if not valid:
        if user and not locked:
            failures = int(user["failed_logins"]) + 1
            locked_until = _iso(now + timedelta(minutes=15)) if failures >= 5 else None
            conn.execute("UPDATE auth_users SET failed_logins=?,locked_until=?,updated_at=? WHERE id=?",
                         (failures, locked_until, _iso(now), user["id"]))
        _event(conn, "login_failed", candidate or "unknown", False,
               actor_user_id=user["id"] if user else None, target_type="user",
               target_key=candidate or None, client_ip=client_ip, user_agent=user_agent,
               details={"locked": locked}, now=now)
        conn.commit()
        raise ValueError("Invalid username or password")
    if PASSWORD_HASHER.check_needs_rehash(user["password_hash"]):
        conn.execute("UPDATE auth_users SET password_hash=?,updated_at=? WHERE id=?",
                     (PASSWORD_HASHER.hash(payload["password"]), _iso(now), user["id"]))
    conn.execute(
        "UPDATE auth_users SET failed_logins=0,locked_until=NULL,last_login_at=?,updated_at=? WHERE id=?",
        (_iso(now), _iso(now), user["id"]),
    )
    user.update({"failed_logins": 0, "locked_until": None, "last_login_at": _iso(now)})
    session = _new_session(conn, user, client_ip, user_agent, now)
    _event(conn, "login_succeeded", user["display_name"], True, actor_user_id=user["id"],
           target_type="user", target_key=user["username"], client_ip=client_ip,
           user_agent=user_agent, now=now)
    conn.commit()
    return session


def authenticate(conn: sqlite3.Connection, *, session_token: Optional[str] = None,
                 bearer_token: Optional[str] = None, now: Optional[datetime] = None) -> Optional[dict]:
    now = now or _now()
    if bearer_token:
        row = conn.execute(
            "SELECT * FROM auth_api_keys WHERE token_hash=? AND active=1",
            (_token_hash(bearer_token),),
        ).fetchone()
        if row:
            key = dict(row)
            if key["expires_at"] and _parse(key["expires_at"]) <= now:
                return None
            conn.execute("UPDATE auth_api_keys SET last_used_at=? WHERE id=?", (_iso(now), key["id"]))
            conn.commit()
            return {"kind": "api_key", "id": key["id"], "name": key["name"],
                    "display_name": key["name"], "permissions": set(json.loads(key["permissions_json"])),
                    "role": "integration", "csrf_token": None, "session_id": None}
        return None
    if not session_token:
        return None
    row = conn.execute(
        """SELECT s.id session_id,s.csrf_token,s.expires_at,u.*
           FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.revoked_at IS NULL AND u.active=1""",
        (_token_hash(session_token),),
    ).fetchone()
    if not row:
        return None
    user = dict(row)
    if _parse(user["expires_at"]) <= now:
        conn.execute("UPDATE auth_sessions SET revoked_at=? WHERE id=?", (_iso(now), user["session_id"]))
        conn.commit()
        return None
    return {"kind": "user", "id": user["id"], "name": user["username"],
            "display_name": user["display_name"], "role": user["role"],
            "permissions": set(ROLE_PERMISSIONS.get(user["role"], set())),
            "csrf_token": user["csrf_token"], "session_id": user["session_id"],
            "user": _public_user(user), "expires_at": user["expires_at"]}


def authorize(principal: Optional[dict], required: tuple[str, ...]) -> bool:
    return bool(principal and set(required) & set(principal["permissions"]))


def required_permissions(method: str, path: str) -> tuple[str, ...]:
    if path in SELF_AUTH_PATHS:
        return tuple(PERMISSIONS)
    if path.startswith(ADMIN_AUTH_PREFIXES):
        return ("admin",)
    if method in {"GET", "HEAD"}:
        return ("view",)
    if path.startswith("/alerts/settings") or path.startswith("/alerts/destinations") or path.startswith("/alerts/deliveries"):
        return ("commission",)
    if path.startswith("/alerts"):
        return ("alerts", "supervise")
    if path in {"/remote-setup/identity", "/remote-setup/trust-host", "/remote-setup/install-agent/live"} \
            or (method == "DELETE" and path.startswith("/remote-setup/trust-host/")):
        return ("admin",)
    if path.startswith("/resilience"):
        return ("admin",)
    if path.startswith(("/config", "/remote-setup", "/industrial", "/cycle-times")):
        return ("commission",)
    if path == "/commissioning/log/analyze":
        return ("commission", "integration")
    if path == "/commissioning-lab/run":
        return ("commission", "optimize")
    if re.fullmatch(r"/connectors/[^/]+/import", path):
        return ("commission", "integration")
    if path.startswith("/connectors"):
        return ("commission",)
    if path.startswith("/procurement"):
        return ("procure",)
    if path.startswith(("/planning", "/production", "/resources", "/inventory", "/recovery")):
        return ("plan",)
    if path.startswith(("/maintenance", "/tooling")):
        return ("maintain",)
    if path.startswith(("/quality", "/rework")):
        return ("quality", "supervise")
    if path == "/barcode/events":
        return ("operate", "integration")
    if path.startswith(("/execution", "/identity", "/labels", "/downtime", "/barcode")):
        return ("operate",)
    if path.startswith(("/improvements", "/root-causes", "/learning", "/digital-twin", "/forecast")):
        return ("optimize", "supervise")
    if path.startswith("/events/simulate"):
        return ("commission",)
    return ("admin",)


def bind_actor(payload: object, principal: dict) -> object:
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    name = principal["display_name"]
    for field in ACTOR_FIELDS:
        if field in result:
            result[field] = name
    return result


def record_request(conn: sqlite3.Connection, principal: dict, method: str, path: str,
                   status_code: int, *, client_ip: Optional[str] = None,
                   user_agent: Optional[str] = None, now: Optional[datetime] = None) -> None:
    _event(conn, "api_request", principal["display_name"], status_code < 400,
           actor_user_id=principal["id"] if principal["kind"] == "user" else None,
           target_type="api", target_key=f"{method} {path}", client_ip=client_ip,
           user_agent=user_agent, details={"status_code": status_code, "principal_kind": principal["kind"]},
           now=now)
    conn.commit()


def revoke_session(conn: sqlite3.Connection, principal: dict, *, now: Optional[datetime] = None) -> None:
    now = now or _now()
    if principal.get("session_id"):
        conn.execute("UPDATE auth_sessions SET revoked_at=? WHERE id=?", (_iso(now), principal["session_id"]))
        _event(conn, "logout", principal["display_name"], True, actor_user_id=principal["id"], now=now)
        conn.commit()


def list_users(conn: sqlite3.Connection) -> list[dict]:
    return [_public_user(dict(row)) for row in conn.execute("SELECT * FROM auth_users ORDER BY display_name")]


def create_user(conn: sqlite3.Connection, payload: dict, principal: dict,
                now: Optional[datetime] = None) -> dict:
    now = now or _now()
    username = _normalize_username(payload.get("username"))
    display_name = _named(payload.get("display_name"))
    role = payload.get("role")
    if role not in ROLE_PERMISSIONS:
        raise ValueError("Unknown access role")
    password = payload.get("password") or ""
    _validate_password(password, username, display_name)
    try:
        cursor = conn.execute(
            """INSERT INTO auth_users
               (username,display_name,password_hash,role,active,password_changed_at,created_by,created_at,updated_at)
               VALUES (?,?,?,?,1,?,?,?,?)""",
            (username, display_name, PASSWORD_HASHER.hash(password), role, _iso(now),
             principal["display_name"], _iso(now), _iso(now)),
        )
    except sqlite3.IntegrityError as error:
        raise ValueError("Username already exists") from error
    _event(conn, "user_created", principal["display_name"], True,
           actor_user_id=principal["id"], target_type="user", target_key=username,
           details={"role": role}, now=now)
    conn.commit()
    return _public_user(dict(conn.execute("SELECT * FROM auth_users WHERE id=?", (cursor.lastrowid,)).fetchone()))


def update_user(conn: sqlite3.Connection, user_id: int, payload: dict, principal: dict,
                now: Optional[datetime] = None) -> dict:
    now = now or _now()
    row = conn.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise KeyError(f"User {user_id} not found")
    current = dict(row)
    if payload.get("expected_version") is not None and int(payload["expected_version"]) != current["version"]:
        raise ValueError("User changed; refresh before saving")
    role = payload.get("role", current["role"])
    if role not in ROLE_PERMISSIONS:
        raise ValueError("Unknown access role")
    active = bool(payload.get("active", current["active"]))
    if current["id"] == principal["id"] and (not active or role != "admin"):
        raise ValueError("An administrator cannot remove their own active admin access")
    display_name = _named(payload.get("display_name", current["display_name"]))
    conn.execute(
        """UPDATE auth_users SET display_name=?,role=?,active=?,version=version+1,updated_at=? WHERE id=?""",
        (display_name, role, int(active), _iso(now), user_id),
    )
    if not active or role != current["role"]:
        conn.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                     (_iso(now), user_id))
    _event(conn, "user_updated", principal["display_name"], True,
           actor_user_id=principal["id"], target_type="user", target_key=current["username"],
           details={"role": role, "active": active}, now=now)
    conn.commit()
    return _public_user(dict(conn.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()))


def reset_password(conn: sqlite3.Connection, user_id: int, password: str, principal: dict,
                   now: Optional[datetime] = None) -> dict:
    now = now or _now()
    row = conn.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise KeyError(f"User {user_id} not found")
    user = dict(row)
    _validate_password(password, user["username"], user["display_name"])
    conn.execute(
        """UPDATE auth_users SET password_hash=?,failed_logins=0,locked_until=NULL,
           password_changed_at=?,version=version+1,updated_at=? WHERE id=?""",
        (PASSWORD_HASHER.hash(password), _iso(now), _iso(now), user_id),
    )
    conn.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                 (_iso(now), user_id))
    _event(conn, "password_reset", principal["display_name"], True,
           actor_user_id=principal["id"], target_type="user", target_key=user["username"], now=now)
    conn.commit()
    return {"reset": True, "username": user["username"], "sessions_revoked": True}


def change_password(conn: sqlite3.Connection, payload: dict, principal: dict,
                    now: Optional[datetime] = None) -> dict:
    if principal["kind"] != "user":
        raise ValueError("Only a human account can change its password")
    now = now or _now()
    row = conn.execute("SELECT * FROM auth_users WHERE id=?", (principal["id"],)).fetchone()
    if not row:
        raise KeyError(f"User {principal['id']} not found")
    user = dict(row)
    try:
        valid = PASSWORD_HASHER.verify(user["password_hash"], payload.get("current_password") or "")
    except (VerifyMismatchError, InvalidHashError):
        valid = False
    if not valid:
        raise ValueError("Current password is incorrect")
    new_password = payload.get("new_password") or ""
    _validate_password(new_password, user["username"], user["display_name"])
    conn.execute(
        """UPDATE auth_users SET password_hash=?,password_changed_at=?,version=version+1,
           updated_at=? WHERE id=?""",
        (PASSWORD_HASHER.hash(new_password), _iso(now), _iso(now), user["id"]),
    )
    conn.execute(
        "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND id!=? AND revoked_at IS NULL",
        (_iso(now), user["id"], principal["session_id"]),
    )
    _event(conn, "password_changed", principal["display_name"], True,
           actor_user_id=principal["id"], target_type="user", target_key=user["username"], now=now)
    conn.commit()
    return {"changed": True, "other_sessions_revoked": True}


def create_api_key(conn: sqlite3.Connection, payload: dict, principal: dict,
                   now: Optional[datetime] = None) -> dict:
    now = now or _now()
    name = _named(payload.get("name"), "Credential name")
    permissions = sorted(set(payload.get("permissions") or []))
    if not permissions or not set(permissions) <= SERVICE_KEY_PERMISSIONS:
        raise ValueError("Machine API keys may only use the integration permission")
    token = "hive_" + secrets.token_urlsafe(32)
    expires_at = payload.get("expires_at")
    if expires_at and _parse(expires_at) <= now:
        raise ValueError("API key expiry must be in the future")
    try:
        cursor = conn.execute(
            """INSERT INTO auth_api_keys
               (name,key_prefix,token_hash,permissions_json,active,expires_at,created_by,created_at)
               VALUES (?,?,?,?,1,?,?,?)""",
            (name, token[:13], _token_hash(token), json.dumps(permissions), expires_at,
             principal["display_name"], _iso(now)),
        )
    except sqlite3.IntegrityError as error:
        raise ValueError("Credential name already exists") from error
    _event(conn, "api_key_created", principal["display_name"], True,
           actor_user_id=principal["id"], target_type="api_key", target_key=str(cursor.lastrowid),
           details={"name": name, "permissions": permissions}, now=now)
    conn.commit()
    return {"id": cursor.lastrowid, "name": name, "token": token, "key_prefix": token[:13],
            "permissions": permissions, "expires_at": expires_at,
            "warning": "This token is shown once. Store it on the intended machine PC."}


def list_api_keys(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for source in conn.execute("SELECT * FROM auth_api_keys ORDER BY name"):
        row = dict(source)
        rows.append({"id": row["id"], "name": row["name"], "key_prefix": row["key_prefix"],
                     "permissions": json.loads(row["permissions_json"]), "active": bool(row["active"]),
                     "expires_at": row["expires_at"], "last_used_at": row["last_used_at"],
                     "created_by": row["created_by"], "created_at": row["created_at"],
                     "version": row["version"]})
    return rows


def revoke_api_key(conn: sqlite3.Connection, key_id: int, principal: dict,
                   now: Optional[datetime] = None) -> dict:
    now = now or _now()
    row = conn.execute("SELECT * FROM auth_api_keys WHERE id=?", (key_id,)).fetchone()
    if not row:
        raise KeyError(f"API key {key_id} not found")
    conn.execute("UPDATE auth_api_keys SET active=0,revoked_at=?,version=version+1 WHERE id=?",
                 (_iso(now), key_id))
    _event(conn, "api_key_revoked", principal["display_name"], True,
           actor_user_id=principal["id"], target_type="api_key", target_key=str(key_id),
           details={"name": row["name"]}, now=now)
    conn.commit()
    return {"revoked": True, "id": key_id, "name": row["name"]}


def recent_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = []
    for source in conn.execute("SELECT * FROM auth_events ORDER BY id DESC LIMIT ?", (limit,)):
        row = dict(source)
        row["success"] = bool(row["success"])
        row["details"] = json.loads(row.pop("details_json"))
        rows.append(row)
    return rows


def snapshot(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    return {
        "setup_required": setup_required(conn) if auth_required() else False,
        "auth_required": auth_required(),
        "users": conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0],
        "active_users": conn.execute("SELECT COUNT(*) FROM auth_users WHERE active=1").fetchone()[0],
        "active_sessions": conn.execute(
            "SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL AND expires_at>?", (_iso(now),)
        ).fetchone()[0],
        "active_api_keys": conn.execute(
            "SELECT COUNT(*) FROM auth_api_keys WHERE active=1 AND (expires_at IS NULL OR expires_at>?)", (_iso(now),)
        ).fetchone()[0],
        "failed_logins_24h": conn.execute(
            "SELECT COUNT(*) FROM auth_events WHERE event_type='login_failed' AND ts>=?",
            (_iso(now - timedelta(hours=24)),),
        ).fetchone()[0],
    }
