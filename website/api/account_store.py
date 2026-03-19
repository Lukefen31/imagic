"""Persistent account, credit, and license storage for imagic web."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Allow override via env var so Fly.io can point at a persistent volume.
_db_override = os.environ.get("IMAGIC_DB_PATH", "").strip()
if _db_override:
    _DB_PATH = Path(_db_override)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
else:
    _DB_PATH = _DATA_DIR / "accounts.db"
_PASSWORD_ITERATIONS = 200_000
_DEFAULT_SESSION_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    raw_salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        raw_salt,
        _PASSWORD_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PASSWORD_ITERATIONS}${raw_salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def _generate_license_key(prefix: str) -> str:
    chunks = [secrets.token_hex(2).upper() for _ in range(4)]
    return f"IMAGIC-{prefix}-" + "-".join(chunks)


class AccountStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        auth_provider TEXT NOT NULL DEFAULT 'local',
                        external_subject TEXT,
                        credit_balance INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS licenses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        license_key TEXT NOT NULL UNIQUE,
                        product_type TEXT NOT NULL,
                        credits_total INTEGER NOT NULL DEFAULT 0,
                        user_id INTEGER,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL,
                        redeemed_at TEXT,
                        last_used_at TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
                    );

                    CREATE TABLE IF NOT EXISTS desktop_activations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        license_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        device_id TEXT NOT NULL,
                        device_name TEXT NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        revoked INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY(license_id) REFERENCES licenses(id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS desktop_purchases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        stripe_session_id TEXT NOT NULL UNIQUE,
                        user_id INTEGER NOT NULL,
                        license_id INTEGER NOT NULL UNIQUE,
                        delivery_email TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        email_sent_at TEXT,
                        email_error TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(license_id) REFERENCES licenses(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS desktop_download_grants (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        license_id INTEGER NOT NULL,
                        variant TEXT NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        revoked INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY(license_id) REFERENCES licenses(id) ON DELETE CASCADE
                    );
                    """
                )
                existing = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                if "auth_provider" not in existing:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'"
                    )
                if "external_subject" not in existing:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN external_subject TEXT"
                    )
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_provider_subject
                    ON users(auth_provider, external_subject)
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def create_user(self, email: str, password: str) -> dict:
        clean_email = _normalise_email(email)
        if not clean_email or "@" not in clean_email:
            raise ValueError("Enter a valid email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO users (
                        email, password_hash, auth_provider, external_subject, created_at
                    ) VALUES (?, ?, 'local', NULL, ?)
                    """,
                    (clean_email, _hash_password(password), _now_iso()),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("An account with that email already exists.") from exc
            finally:
                conn.close()
        user = self.get_user_by_email(clean_email)
        if user is None:
            raise ValueError("Account could not be created.")
        return user

    def get_user_by_email(self, email: str) -> Optional[dict]:
        clean_email = _normalise_email(email)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, email, credit_balance, created_at FROM users WHERE email = ?",
                    (clean_email,),
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def _get_user_auth_row(self, email: str) -> Optional[sqlite3.Row]:
        clean_email = _normalise_email(email)
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, email, password_hash, credit_balance, created_at FROM users WHERE email = ?",
                (clean_email,),
            ).fetchone()
        finally:
            conn.close()

    def authenticate_user(self, email: str, password: str) -> Optional[dict]:
        row = self._get_user_auth_row(email)
        if not row or not _verify_password(password, row["password_hash"]):
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "credit_balance": row["credit_balance"],
            "created_at": row["created_at"],
        }

    def get_or_create_oauth_user(
        self,
        email: str,
        provider: str,
        external_subject: str,
    ) -> dict:
        clean_email = _normalise_email(email)
        if not clean_email or "@" not in clean_email:
            raise ValueError("OAuth provider did not return a valid email address.")
        provider = provider.strip().lower()
        if not provider or not external_subject:
            raise ValueError("OAuth identity is incomplete.")

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, email, credit_balance, created_at FROM users WHERE auth_provider = ? AND external_subject = ?",
                    (provider, external_subject),
                ).fetchone()
                if row:
                    return dict(row)

                row = conn.execute(
                    "SELECT id, email, credit_balance, created_at FROM users WHERE email = ?",
                    (clean_email,),
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE users SET auth_provider = ?, external_subject = ? WHERE id = ?",
                        (provider, external_subject, row["id"]),
                    )
                    conn.commit()
                    return dict(row)

                conn.execute(
                    """
                    INSERT INTO users (
                        email, password_hash, auth_provider, external_subject, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        clean_email,
                        _hash_password(secrets.token_urlsafe(24)),
                        provider,
                        external_subject,
                        _now_iso(),
                    ),
                )
                conn.commit()
                created = conn.execute(
                    "SELECT id, email, credit_balance, created_at FROM users WHERE email = ?",
                    (clean_email,),
                ).fetchone()
            finally:
                conn.close()
        return dict(created) if created else self.get_user_by_email(clean_email)

    def create_session(self, user_id: int, days: int = _DEFAULT_SESSION_DAYS) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = (_now() + timedelta(days=days)).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO sessions (user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    (user_id, _hash_secret(token), _now_iso(), expires_at),
                )
                conn.commit()
            finally:
                conn.close()
        return token

    def delete_session(self, token: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_secret(token),))
                conn.commit()
            finally:
                conn.close()

    def get_user_by_session(self, token: str) -> Optional[dict]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT u.id, u.email, u.credit_balance, u.created_at
                    FROM sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = ? AND s.expires_at > ?
                    """,
                    (_hash_secret(token), now),
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def get_credit_balance(self, user_id: int) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT credit_balance FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
            finally:
                conn.close()
        return int(row["credit_balance"]) if row else 0

    def add_credits(self, user_id: int, count: int) -> int:
        if count <= 0:
            return self.get_credit_balance(user_id)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE users SET credit_balance = credit_balance + ? WHERE id = ?",
                    (count, user_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT credit_balance FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
            finally:
                conn.close()
        return int(row["credit_balance"]) if row else 0

    def consume_credits(self, user_id: int, count: int) -> bool:
        if count <= 0:
            return True
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT credit_balance FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if row is None or int(row["credit_balance"]) < count:
                    return False
                conn.execute(
                    "UPDATE users SET credit_balance = credit_balance - ? WHERE id = ?",
                    (count, user_id),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def _get_or_create_purchase_user(self, conn: sqlite3.Connection, email: str) -> sqlite3.Row:
        clean_email = _normalise_email(email)
        if not clean_email or "@" not in clean_email:
            raise ValueError("Enter a valid email address.")

        row = conn.execute(
            "SELECT id, email, credit_balance, created_at FROM users WHERE email = ?",
            (clean_email,),
        ).fetchone()
        if row:
            return row

        conn.execute(
            """
            INSERT INTO users (
                email, password_hash, auth_provider, external_subject, created_at
            ) VALUES (?, ?, 'local', NULL, ?)
            """,
            (
                clean_email,
                _hash_password(secrets.token_urlsafe(32)),
                _now_iso(),
            ),
        )
        created = conn.execute(
            "SELECT id, email, credit_balance, created_at FROM users WHERE email = ?",
            (clean_email,),
        ).fetchone()
        if created is None:
            raise ValueError("Could not prepare a purchaser account.")
        return created

    def issue_license(
        self,
        product_type: str,
        credits_total: int = 0,
        user_id: Optional[int] = None,
    ) -> dict:
        prefix = "WEB" if product_type == "web_credit" else "DESK"
        license_key = _generate_license_key(prefix)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO licenses (
                        license_key, product_type, credits_total, user_id, status, created_at
                    ) VALUES (?, ?, ?, ?, 'active', ?)
                    """,
                    (license_key, product_type, credits_total, user_id, _now_iso()),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM licenses WHERE license_key = ?",
                    (license_key,),
                ).fetchone()
            finally:
                conn.close()
        return dict(row)

    def fulfill_desktop_purchase(self, stripe_session_id: str, email: str) -> dict:
        clean_session_id = stripe_session_id.strip()
        clean_email = _normalise_email(email)
        if not clean_session_id:
            raise ValueError("Stripe session id is required.")
        if not clean_email or "@" not in clean_email:
            raise ValueError("A valid delivery email is required.")

        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    """
                    SELECT p.stripe_session_id, p.delivery_email, p.created_at, p.email_sent_at,
                           p.email_error, p.license_id,
                           l.license_key,
                           u.email AS purchaser_email
                    FROM desktop_purchases p
                    JOIN licenses l ON l.id = p.license_id
                    JOIN users u ON u.id = p.user_id
                    WHERE p.stripe_session_id = ?
                    """,
                    (clean_session_id,),
                ).fetchone()
                if existing:
                    return dict(existing)

                user = self._get_or_create_purchase_user(conn, clean_email)
                license_key = _generate_license_key("DESK")
                created_at = _now_iso()
                conn.execute(
                    """
                    INSERT INTO licenses (
                        license_key, product_type, credits_total, user_id, status, created_at
                    ) VALUES (?, 'desktop', 0, ?, 'active', ?)
                    """,
                    (license_key, user["id"], created_at),
                )
                license_row = conn.execute(
                    "SELECT id, license_key FROM licenses WHERE license_key = ?",
                    (license_key,),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO desktop_purchases (
                        stripe_session_id, user_id, license_id, delivery_email, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (clean_session_id, user["id"], license_row["id"], clean_email, created_at),
                )
                conn.commit()
                return {
                    "stripe_session_id": clean_session_id,
                    "delivery_email": clean_email,
                    "created_at": created_at,
                    "email_sent_at": None,
                    "email_error": None,
                    "license_id": int(license_row["id"]),
                    "license_key": str(license_row["license_key"]),
                    "purchaser_email": str(user["email"]),
                }
            finally:
                conn.close()

    def get_desktop_purchase(self, stripe_session_id: str) -> Optional[dict]:
        clean_session_id = stripe_session_id.strip()
        if not clean_session_id:
            return None
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT p.stripe_session_id, p.delivery_email, p.created_at, p.email_sent_at,
                           p.email_error, p.license_id,
                           l.license_key,
                           u.email AS purchaser_email
                    FROM desktop_purchases p
                    JOIN licenses l ON l.id = p.license_id
                    JOIN users u ON u.id = p.user_id
                    WHERE p.stripe_session_id = ?
                    """,
                    (clean_session_id,),
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def mark_desktop_purchase_email_result(
        self,
        stripe_session_id: str,
        *,
        sent: bool,
        error_message: str = "",
    ) -> None:
        clean_session_id = stripe_session_id.strip()
        if not clean_session_id:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE desktop_purchases
                    SET email_sent_at = ?, email_error = ?
                    WHERE stripe_session_id = ?
                    """,
                    (_now_iso() if sent else None, error_message or None, clean_session_id),
                )
                conn.commit()
            finally:
                conn.close()

    def issue_desktop_download(self, stripe_session_id: str, variant: str) -> dict:
        clean_session_id = stripe_session_id.strip()
        clean_variant = variant.strip().lower()
        if clean_variant not in {"standard", "rawtherapee", "standard_macos", "rawtherapee_macos"}:
            raise ValueError("Unknown desktop download variant.")

        token = secrets.token_urlsafe(32)
        with self._lock:
            conn = self._connect()
            try:
                purchase = conn.execute(
                    """
                    SELECT p.stripe_session_id, p.delivery_email, p.license_id,
                           l.license_key,
                           u.email AS purchaser_email
                    FROM desktop_purchases p
                    JOIN licenses l ON l.id = p.license_id
                    JOIN users u ON u.id = p.user_id
                    WHERE p.stripe_session_id = ?
                    """,
                    (clean_session_id,),
                ).fetchone()
                if purchase is None:
                    raise ValueError("Desktop purchase not found.")
                conn.execute(
                    """
                    INSERT INTO desktop_download_grants (
                        license_id, variant, token_hash, created_at, revoked
                    ) VALUES (?, ?, ?, ?, 0)
                    """,
                    (purchase["license_id"], clean_variant, _hash_secret(token), _now_iso()),
                )
                conn.commit()
                return {
                    "token": token,
                    "variant": clean_variant,
                    "license_key": str(purchase["license_key"]),
                    "delivery_email": str(purchase["delivery_email"]),
                    "purchaser_email": str(purchase["purchaser_email"]),
                }
            finally:
                conn.close()

    def resolve_desktop_download(self, token: str) -> Optional[dict]:
        clean_token = token.strip()
        if not clean_token:
            return None
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT g.variant,
                           l.license_key,
                           p.delivery_email,
                           u.email AS purchaser_email
                    FROM desktop_download_grants g
                    JOIN licenses l ON l.id = g.license_id
                    JOIN desktop_purchases p ON p.license_id = l.id
                    JOIN users u ON u.id = p.user_id
                    WHERE g.token_hash = ? AND g.revoked = 0
                    """,
                    (_hash_secret(clean_token),),
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def redeem_credit_license(self, user_id: int, license_key: str) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM licenses WHERE license_key = ?",
                    (license_key.strip().upper(),),
                ).fetchone()
                if row is None:
                    raise ValueError("License key not found.")
                if row["product_type"] != "web_credit":
                    raise ValueError("That key is not a web credit key.")
                if row["status"] != "active":
                    raise ValueError("That key is no longer active.")
                if row["redeemed_at"]:
                    raise ValueError("That credit key has already been redeemed.")
                conn.execute(
                    "UPDATE users SET credit_balance = credit_balance + ? WHERE id = ?",
                    (int(row["credits_total"]), user_id),
                )
                conn.execute(
                    """
                    UPDATE licenses
                    SET user_id = ?, redeemed_at = ?, last_used_at = ?, status = 'redeemed'
                    WHERE id = ?
                    """,
                    (user_id, _now_iso(), _now_iso(), row["id"]),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT credit_balance FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
            finally:
                conn.close()
        return int(updated["credit_balance"]) if updated else 0

    def activate_desktop_license(
        self,
        license_key: str,
        device_id: str,
        device_name: str,
    ) -> dict:
        clean_key = license_key.strip().upper()
        token = secrets.token_urlsafe(32)
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                lic = conn.execute(
                    "SELECT * FROM licenses WHERE license_key = ?",
                    (clean_key,),
                ).fetchone()
                if lic is None:
                    raise ValueError("License key not found.")
                if lic["product_type"] != "desktop":
                    raise ValueError("That key is not a desktop license.")
                if lic["status"] != "active":
                    raise ValueError("That license is not active.")
                if lic["user_id"] is None:
                    raise ValueError("That license has no purchaser email attached. Contact support.")

                owner = conn.execute(
                    "SELECT id, email FROM users WHERE id = ?",
                    (lic["user_id"],),
                ).fetchone()
                if owner is None:
                    raise ValueError("That license owner could not be found.")

                conn.execute(
                    "UPDATE licenses SET last_used_at = ? WHERE id = ?",
                    (now, lic["id"]),
                )
                conn.execute(
                    "UPDATE desktop_activations SET revoked = 1 WHERE license_id = ? AND revoked = 0",
                    (lic["id"],),
                )
                conn.execute(
                    """
                    INSERT INTO desktop_activations (
                        license_id, user_id, device_id, device_name, token_hash,
                        created_at, last_seen_at, revoked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (lic["id"], owner["id"], device_id, device_name, _hash_secret(token), now, now),
                )
                conn.commit()
            finally:
                conn.close()
        return {
            "activation_token": token,
            "email": str(owner["email"]),
            "license_key": clean_key,
        }

    def validate_activation(self, token: str, device_id: str) -> Optional[dict]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT a.id, a.device_id, u.email, l.license_key
                    FROM desktop_activations a
                    JOIN users u ON u.id = a.user_id
                    JOIN licenses l ON l.id = a.license_id
                    WHERE a.token_hash = ? AND a.revoked = 0 AND l.status = 'active'
                    """,
                    (_hash_secret(token),),
                ).fetchone()
                if row is None or row["device_id"] != device_id:
                    return None
                conn.execute(
                    "UPDATE desktop_activations SET last_seen_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                conn.execute(
                    "UPDATE licenses SET last_used_at = ? WHERE license_key = ?",
                    (now, row["license_key"]),
                )
                conn.commit()
            finally:
                conn.close()
        return {
            "email": row["email"],
            "license_key": row["license_key"],
            "device_id": row["device_id"],
        }

    def get_all_desktop_purchases(self) -> list[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT p.stripe_session_id, p.delivery_email, p.created_at, p.email_sent_at,
                           p.email_error, p.license_id,
                           l.license_key,
                           u.email AS purchaser_email
                    FROM desktop_purchases p
                    JOIN licenses l ON l.id = p.license_id
                    JOIN users u ON u.id = p.user_id
                    ORDER BY p.created_at DESC
                    """
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def get_sales_analytics(self) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                total_sales = conn.execute(
                    "SELECT COUNT(*) as count FROM desktop_purchases"
                ).fetchone()["count"]
                
                # Simple analytics: sales per day for last 30 days
                sales_by_day = conn.execute(
                    """
                    SELECT date(created_at) as day, COUNT(*) as count
                    FROM desktop_purchases
                    WHERE created_at > date('now', '-30 days')
                    GROUP BY day
                    ORDER BY day DESC
                    """
                ).fetchall()

                # Variant distribution (from grants)
                variant_stats = conn.execute(
                    """
                    SELECT variant, COUNT(*) as count
                    FROM desktop_download_grants
                    GROUP BY variant
                    """
                ).fetchall()
            finally:
                conn.close()
        
        return {
            "total_sales": total_sales,
            "sales_by_day": [dict(r) for r in sales_by_day],
            "variant_stats": [dict(r) for r in variant_stats],
        }


account_store = AccountStore()