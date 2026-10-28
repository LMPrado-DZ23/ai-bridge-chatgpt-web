"""
Control-token authentication for admin endpoints.

The token is a 40+ char base64url string, auto-generated on first use and
persisted in config.json. Requests carrying an Origin header are rejected
outright to prevent browser-based CSRF.
"""
import hmac
import secrets
from functools import wraps

from flask import request, jsonify

from . import config as cfg

_TOKEN_KEY = 'control_token'
_MIN_LEN = 40


def get_token() -> str:
    """Return the control token, creating and persisting it if absent."""
    data = cfg.load()
    token = data.get(_TOKEN_KEY, '')
    if not token or len(token) < _MIN_LEN:
        token = secrets.token_urlsafe(_MIN_LEN)
        data[_TOKEN_KEY] = token
        cfg.save(data)
    return token


def require_admin(f):
    """Decorator: enforce control-token auth on admin endpoints."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        # Block all browser-originated requests (CSRF protection)
        if request.headers.get('Origin'):
            return jsonify({'error': 'forbidden'}), 403
        provided = request.headers.get('X-Bridge-Token', '')
        expected = get_token()
        if not provided or not hmac.compare_digest(provided, expected):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapped
