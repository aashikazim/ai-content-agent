"""Single local administrator account and revocable browser sessions.

Only password hashes are stored in the local credential file. Session cookies
contain random secrets; the database stores their SHA-256 digests instead.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path


class AuthInvalidCredentials(ValueError):
    def __init__(self):
        super().__init__("Invalid username or password")


class AuthNotConfigured(RuntimeError):
    def __init__(self):
        super().__init__("Administrator account is not configured")


class AuthRateLimited(RuntimeError):
    def __init__(self, retry_after):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__("Too many login attempts. Please try again later.")


class AdminAuthService:
    # This scrypt configuration uses approximately 32 MiB and three passes.
    SCRYPT_N = 2 ** 15
    SCRYPT_R = 8
    SCRYPT_P = 3
    KEY_LENGTH = 64
    FAILURE_LIMIT = 5
    FAILURE_WINDOW = 15 * 60
    LOCKOUT_SECONDS = 15 * 60

    def __init__(self, db_path=None, credentials_path=None, clock=None):
        self.db_path = str(
            db_path if db_path is not None
            else os.getenv("DATABASE_PATH", "data/trends.db")
        )
        self.credentials_path = Path(
            credentials_path if credentials_path is not None
            else os.getenv("ADMIN_CREDENTIALS_FILE", "credentials/admin.json")
        )
        self.clock = clock if clock is not None else time.time
        hours = float(os.getenv("ADMIN_SESSION_HOURS", "12"))
        if not math.isfinite(hours) or not 1 <= hours <= 168:
            raise ValueError("ADMIN_SESSION_HOURS must be between 1 and 168")
        self.session_seconds = int(hours * 3600)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self):
        """Add independent auth tables without changing content/history tables."""
        with self.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    credential_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry
                ON admin_sessions(expires_at)
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS admin_login_attempts (
                    client_hash TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL,
                    first_failure_at INTEGER NOT NULL,
                    blocked_until INTEGER NOT NULL
                )
            """)

    @staticmethod
    def _digest(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _password_hash(cls, password, salt):
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=cls.SCRYPT_N, r=cls.SCRYPT_R, p=cls.SCRYPT_P,
            dklen=cls.KEY_LENGTH, maxmem=64 * 1024 * 1024,
        )

    @staticmethod
    def _valid_username(username):
        return isinstance(username, str) and bool(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{2,63}", username)
        )

    @staticmethod
    def _valid_password(password):
        if (
            not isinstance(password, str)
            or not 12 <= len(password) <= 1024
            or not password.strip()
        ):
            return False
        try:
            password.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True

    def _read_credentials(self):
        try:
            # Bound reads so a malformed file cannot cause unbounded allocation.
            with self.credentials_path.open("r", encoding="utf-8") as credential_file:
                encoded = credential_file.read(4097)
            if len(encoded) > 4096:
                return None
            document = json.loads(encoded)
            parameters = document["scrypt"]
            if (
                document["version"] != 1
                or not self._valid_username(document["username"])
                or not re.fullmatch(r"[0-9a-f]{64}", document["credential_id"])
                or parameters["n"] != self.SCRYPT_N
                or parameters["r"] != self.SCRYPT_R
                or parameters["p"] != self.SCRYPT_P
                or parameters["dklen"] != self.KEY_LENGTH
            ):
                return None
            salt = base64.b64decode(parameters["salt"], validate=True)
            password_hash = base64.b64decode(parameters["hash"], validate=True)
            if len(salt) != 16 or len(password_hash) != self.KEY_LENGTH:
                return None
            return {**document, "salt": salt, "password_hash": password_hash}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @property
    def configured(self):
        return self._read_credentials() is not None

    def configure_admin(self, username, password, reset=False):
        """Create the sole administrator, or explicitly replace it locally."""
        if not self._valid_username(username):
            raise ValueError(
                "Username must be 3–64 letters, digits, dots, underscores, @ or hyphens"
            )
        if not self._valid_password(password):
            raise ValueError("Password must contain between 12 and 1024 characters")
        if self.credentials_path.is_symlink():
            raise ValueError("The administrator credential file cannot be a symlink")
        if self.credentials_path.exists() and not reset:
            raise ValueError("Administrator already configured; use --reset to replace it")

        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt)
        document = {
            "version": 1,
            "username": username,
            "credential_id": secrets.token_hex(32),
            "scrypt": {
                "n": self.SCRYPT_N,
                "r": self.SCRYPT_R,
                "p": self.SCRYPT_P,
                "dklen": self.KEY_LENGTH,
                "salt": base64.b64encode(salt).decode("ascii"),
                "hash": base64.b64encode(password_hash).decode("ascii"),
            },
        }
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        serialized = json.dumps(document, indent=2) + "\n"
        if reset:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".admin-", dir=self.credentials_path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as credential_file:
                    os.fchmod(credential_file.fileno(), 0o600)
                    credential_file.write(serialized)
                    credential_file.flush()
                    os.fsync(credential_file.fileno())
                os.replace(temporary_name, self.credentials_path)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
        else:
            try:
                descriptor = os.open(
                    self.credentials_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError as exc:
                raise ValueError("Administrator already configured; use --reset to replace it") from exc
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as credential_file:
                    os.fchmod(credential_file.fileno(), 0o600)
                    credential_file.write(serialized)
                    credential_file.flush()
                    os.fsync(credential_file.fileno())
            except Exception:
                self.credentials_path.unlink(missing_ok=True)
                raise

        # Credential IDs also invalidate old sessions if a reset races a login.
        with self.connect() as connection:
            connection.execute("DELETE FROM admin_sessions")
            connection.execute("DELETE FROM admin_login_attempts")

    def login(self, username, password, client_id):
        credentials = self._read_credentials()
        if credentials is None:
            raise AuthNotConfigured()
        now = int(self.clock())
        client_hash = self._digest(str(client_id)[:512])
        invalid_credentials = False
        with self.connect() as connection:
            # Serialize check + failed-attempt update across all API workers.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                "DELETE FROM admin_login_attempts WHERE first_failure_at <= ? AND blocked_until <= ?",
                (now - self.FAILURE_WINDOW, now),
            )
            attempt = connection.execute(
                "SELECT * FROM admin_login_attempts WHERE client_hash = ?", (client_hash,)
            ).fetchone()
            if attempt is not None and attempt["blocked_until"] > now:
                raise AuthRateLimited(attempt["blocked_until"] - now)

            password_valid = self._valid_password(password)
            bounded_password = password if password_valid else "invalid-password"
            candidate = self._password_hash(bounded_password, credentials["salt"])
            password_matches = hmac.compare_digest(candidate, credentials["password_hash"])
            username_matches = self._valid_username(username) and hmac.compare_digest(
                username.encode("utf-8"), credentials["username"].encode("utf-8")
            )
            valid = (
                username_matches and password_matches
                and password_valid
            )
            if not valid:
                failures = (attempt["failures"] if attempt is not None else 0) + 1
                first_failure = attempt["first_failure_at"] if attempt is not None else now
                blocked_until = now + self.LOCKOUT_SECONDS if failures >= self.FAILURE_LIMIT else 0
                connection.execute("""
                    INSERT INTO admin_login_attempts
                        (client_hash, failures, first_failure_at, blocked_until)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(client_hash) DO UPDATE SET
                        failures=excluded.failures, first_failure_at=excluded.first_failure_at,
                        blocked_until=excluded.blocked_until
                """, (client_hash, failures, first_failure, blocked_until))
                invalid_credentials = True
            else:
                token = secrets.token_urlsafe(32)
                csrf_token = secrets.token_urlsafe(32)
                expires_at = now + self.session_seconds
                connection.execute("""
                    INSERT INTO admin_sessions
                        (token_hash, username, csrf_token, credential_id, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    self._digest(token), credentials["username"], csrf_token,
                    credentials["credential_id"], now, expires_at,
                ))
                connection.execute(
                    "DELETE FROM admin_login_attempts WHERE client_hash = ?", (client_hash,)
                )

        # Raise after the failed-attempt transaction has committed.
        if invalid_credentials:
            raise AuthInvalidCredentials()
        return {
            "token": token, "csrf_token": csrf_token,
            "username": credentials["username"], "expires_at": expires_at,
        }

    def get_session(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        credentials = self._read_credentials()
        if credentials is None:
            return None
        now = int(self.clock())
        with self.connect() as connection:
            connection.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
            row = connection.execute(
                "SELECT * FROM admin_sessions WHERE token_hash = ?", (self._digest(token),)
            ).fetchone()
        if row is None or row["credential_id"] != credentials["credential_id"]:
            return None
        return {
            "username": row["username"], "csrf_token": row["csrf_token"],
            "expires_at": row["expires_at"],
        }

    def logout(self, token):
        if not isinstance(token, str) or len(token) > 512:
            return
        with self.connect() as connection:
            connection.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (self._digest(token),))
