"""
Authentication helpers - standard library only (no extra dependencies).

  * Passwords: PBKDF2-HMAC-SHA256, per-user random salt, 200k iterations.
  * Sessions : signed token (HMAC-SHA256) stored in an HttpOnly cookie.
               The token carries a fingerprint of the password hash, so changing
               or resetting a password signs that user out everywhere else.
  * Lockout  : 5 failed logins for the same username+IP => 5 minute lock.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

COOKIE_NAME = "wlb_session"
TOKEN_TTL = 8 * 60 * 60            # 8 hours
PBKDF2_ROUNDS = 200_000

MAX_ATTEMPTS = 5
LOCK_SECONDS = 300

_SECRET_FILE = Path(__file__).resolve().parent / ".secret_key"


def is_production() -> bool:
    return os.getenv("WLB_ENV", "").lower() == "production"


def _load_secret() -> bytes:
    env = os.getenv("WLB_SECRET_KEY")
    if env:
        if is_production() and len(env) < 32:
            raise RuntimeError("WLB_SECRET_KEY must be at least 32 characters in production.")
        return env.encode()
    if is_production():
        # an on-disk key would be lost on every redeploy (ephemeral disk) and sign everyone out
        raise RuntimeError("Set WLB_SECRET_KEY (32+ random characters) when WLB_ENV=production.")
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip().encode()
    key = secrets.token_hex(32)
    _SECRET_FILE.write_text(key)
    try:
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        pass
    return key.encode()


SECRET = _load_secret()


# ----------------------------- passwords ----------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def burn_time():
    """Hash once so 'unknown user' takes as long as 'wrong password'."""
    verify_password("x", _DUMMY_HASH)


def validate_password_strength(password: str) -> Optional[str]:
    """Return an error message, or None if the password is acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one digit."
    return None


def generate_temp_password() -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(10))
        if validate_password_strength(pw) is None:
            return pw


# ------------------------------ tokens ------------------------------------
def _fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:12]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user_id: int, password_hash: str, ttl: int = TOKEN_TTL) -> str:
    payload = {"uid": user_id, "pv": _fingerprint(password_hash), "exp": int(time.time()) + ttl}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> Optional[dict]:
    """Return the payload if the signature is valid and not expired, else None."""
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(body))
        if payload["exp"] < time.time():
            return None
        return payload
    except Exception:
        return None


def token_matches_user(payload: dict, user) -> bool:
    return payload.get("uid") == user.id and payload.get("pv") == _fingerprint(user.password_hash)


# ------------------------------ lockout -----------------------------------
_failures: dict = {}      # key -> [count, locked_until]


def check_lockout(key: str):
    rec = _failures.get(key)
    if rec and rec[1] > time.time():
        wait = int(rec[1] - time.time()) + 1
        raise HTTPException(429, f"Too many failed attempts. Try again in {wait} seconds.")


def register_failure(key: str):
    rec = _failures.setdefault(key, [0, 0.0])
    if rec[1] and rec[1] <= time.time():       # previous lock expired -> start fresh
        rec[0], rec[1] = 0, 0.0
    rec[0] += 1
    if rec[0] >= MAX_ATTEMPTS:
        rec[1] = time.time() + LOCK_SECONDS


def clear_failures(key: str):
    _failures.pop(key, None)
