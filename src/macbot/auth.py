"""
MacBot Authentication Module - JWT-based API security
"""
import os
import jwt
import time
import hashlib
import secrets
from typing import Optional, Dict, Any, List
from functools import wraps
from flask import request, jsonify, current_app
from . import config as cfg
from .logging_utils import setup_logger

logger = setup_logger("macbot.auth", "logs/macbot.log")

class AuthenticationError(Exception):
    """Base authentication exception"""
    pass

class InvalidTokenError(AuthenticationError):
    """Raised when JWT token is invalid"""
    pass

class TokenExpiredError(AuthenticationError):
    """Raised when JWT token has expired"""
    pass

class AuthenticationManager:
    """Centralized authentication management for MacBot"""

    def __init__(self):
        self._secret_key = self._get_secret_key()
        self._algorithm = "HS256"
        self._token_expiry = int(os.getenv("MACBOT_TOKEN_EXPIRY", "3600"))  # 1 hour default
        self._api_tokens = self._load_api_tokens()

    def _get_secret_key(self) -> str:
        """Get or generate JWT secret key"""
        secret = os.getenv("MACBOT_JWT_SECRET")
        if not secret:
            # Generate a secure random secret if not provided
            secret = secrets.token_hex(32)
            logger.warning("Using generated JWT secret. Set MACBOT_JWT_SECRET environment variable for production.")
        return secret

    def _load_api_tokens(self) -> set[str]:
        """Load valid API tokens from configuration and environment"""

        def _split_env_tokens(value: str) -> List[str]:
            return [token.strip() for token in value.split(",") if token.strip()]

        raw_tokens: List[str] = []

        for env_var in ("MACBOT_API_TOKENS", "MACBOT_RAG_API_TOKENS"):
            env_tokens = os.getenv(env_var, "")
            if env_tokens:
                raw_tokens.extend(_split_env_tokens(env_tokens))

        try:
            config_tokens = cfg.get_rag_api_tokens()
        except Exception:
            config_tokens = []

        for token in config_tokens:
            if isinstance(token, str):
                stripped = token.strip()
                if stripped:
                    raw_tokens.append(stripped)

        hashed_tokens = {
            self.hash_api_token(token)
            for token in raw_tokens
            if token and not self._is_placeholder_token(token)
        }

        if hashed_tokens:
            return hashed_tokens

        default_token = secrets.token_hex(16)
        logger.warning(
            "Using generated API token: %s. Set MACBOT_API_TOKENS or MACBOT_RAG_API_TOKENS environment variables for production.",
            default_token,
        )
        return {self.hash_api_token(default_token)}

    @staticmethod
    def _is_placeholder_token(token: str) -> bool:
        """Return True when the provided token is a placeholder value"""

        stripped = token.strip()
        if not stripped:
            return True

        normalized = stripped.lower()
        alnum_only = "".join(ch for ch in normalized if ch.isalnum())

        placeholder_markers = {
            "changeme",
            "changethis",
            "addtokenhere",
            "yourtokenhere",
            "yourapitoken",
            "placeholder",
            "exampletoken",
            "replaceme",
            "setsecuretoken",
            "defaulttoken",
        }

        if alnum_only in placeholder_markers:
            return True

        return normalized.startswith("<") and normalized.endswith(">")

    def generate_token(self, user_id: str = "macbot", permissions: Optional[List[str]] = None) -> str:
        """Generate a new JWT token"""
        if permissions is None:
            permissions = ["read", "write"]

        payload = {
            "user_id": user_id,
            "permissions": permissions,
            "iat": int(time.time()),
            "exp": int(time.time()) + self._token_expiry
        }

        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def verify_token(self, token: str) -> Dict[str, Any]:
        """Verify and decode JWT token"""
        try:
            payload = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])

            # Check if token is expired
            if payload.get("exp", 0) < time.time():
                raise TokenExpiredError("Token has expired")

            return payload
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError("Token has expired")
        except jwt.InvalidTokenError:
            raise InvalidTokenError("Invalid token")

    def verify_api_token(self, token: str) -> bool:
        """Verify API token against stored hash"""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token_hash in self._api_tokens

    def hash_api_token(self, token: str) -> str:
        """Hash an API token for storage"""
        return hashlib.sha256(token.encode()).hexdigest()

_auth_manager_instance: Optional[AuthenticationManager] = None

def get_auth_manager() -> AuthenticationManager:
    """Get or create authentication manager instance"""
    global _auth_manager_instance
    if _auth_manager_instance is None:
        _auth_manager_instance = AuthenticationManager()
    return _auth_manager_instance

def require_auth(f):
    """Decorator to require JWT authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_manager = get_auth_manager()

        # Check for JWT token in Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Missing authorization header"}), 401

        if not auth_header.startswith('Bearer '):
            return jsonify({"error": "Invalid authorization header format"}), 401

        token = auth_header.split(' ')[1]
        try:
            payload = auth_manager.verify_token(token)
            # Store auth info in Flask g object for the request context
            from flask import g
            g.user_id = payload.get('user_id')
            g.permissions = payload.get('permissions', [])
        except AuthenticationError as e:
            logger.warning(f"Authentication failed: {e}")
            return jsonify({"error": str(e)}), 401

        return f(*args, **kwargs)
    return decorated_function

def require_api_key(f):
    """Decorator to require API key authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_manager = get_auth_manager()

        # Check for API key in X-API-Key header
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return jsonify({"error": "Missing API key"}), 401

        if not auth_manager.verify_api_token(api_key):
            logger.warning("Invalid API key provided")
            return jsonify({"error": "Invalid API key"}), 401

        return f(*args, **kwargs)
    return decorated_function

def optional_auth(f):
    """Decorator for optional authentication with automatic local auth"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_manager = get_auth_manager()
        from flask import g

        # Auto-authenticate localhost requests (user-friendly for local usage)
        if request.remote_addr in ['127.0.0.1', 'localhost', '::1']:
            g.authenticated = True
            g.user_id = 'localhost_user'
            g.permissions = ['read', 'write']
            g.local_auth = True
            return f(*args, **kwargs)

        # Try JWT authentication first
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                payload = auth_manager.verify_token(token)
                g.user_id = payload.get('user_id')
                g.permissions = payload.get('permissions', [])
                g.authenticated = True
                return f(*args, **kwargs)
            except AuthenticationError:
                pass  # Fall through to unauthenticated

        # Try API key authentication
        api_key = request.headers.get('X-API-Key')
        if api_key and auth_manager.verify_api_token(api_key):
            g.authenticated = True
            g.api_key = True
            return f(*args, **kwargs)

        # No authentication provided
        g.authenticated = False
        g.user_id = None
        g.permissions = []
        return f(*args, **kwargs)
    return decorated_function
