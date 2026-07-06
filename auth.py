"""
auth.py — Minimal shared-password auth using a signed HMAC cookie.
Set SHARED_ACCESS_PASSWORD env var to enable auth.
If the env var is not set, all routes are publicly accessible.
"""

import hashlib
import hmac
import os
import time

from fastapi import Request

SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(32).hex())
SHARED_PASSWORD = os.environ.get("SHARED_ACCESS_PASSWORD", "")
COOKIE_NAME = "novel_tts_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _sign(timestamp: int) -> str:
    msg = f"auth:{timestamp}"
    return hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_auth_cookie() -> str:
    ts = int(time.time())
    return f"{ts}:{_sign(ts)}"


def verify_cookie(value: str) -> bool:
    try:
        ts_str, sig = value.split(":", 1)
        ts = int(ts_str)
        if time.time() - ts > COOKIE_MAX_AGE:
            return False
        return hmac.compare_digest(sig, _sign(ts))
    except Exception:
        return False


def is_authenticated(request: Request) -> bool:
    if not SHARED_PASSWORD:
        return True
    return verify_cookie(request.cookies.get(COOKIE_NAME, ""))


def check_password(password: str) -> bool:
    return bool(SHARED_PASSWORD) and hmac.compare_digest(password, SHARED_PASSWORD)
