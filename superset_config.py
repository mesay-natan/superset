from flask_appbuilder.security.manager import AUTH_DB
import os
from urllib.parse import urlparse

# Import your custom security manager
try:
    from custom_security_manager import CustomSecurityManager
    CUSTOM_SECURITY_MANAGER = CustomSecurityManager
    print("✓ CustomSecurityManager loaded")
except ImportError as e:
    print(f"✗ CustomSecurityManager import error: {e}")
    CUSTOM_SECURITY_MANAGER = None

# All configuration reads from environment variables.
# In Docker: passed via docker-compose.yml (which reads root .env)
# Locally: set in shell or via dotenv

SECRET_KEY = os.getenv('SUPERSET_SECRET_KEY', '')
KEYCLOAK_PUBLIC_KEY = os.getenv('KEYCLOAK_PUBLIC_KEY', '')

# Public URL used by browsers to reach Superset (guest-token JWT `aud`, redirects, embedded URLs).
# Use http://localhost:9196 for local Docker (port mapped to host). Override in production.
_SUPERSET_PUBLIC_URL = (
    os.getenv("SUPERSET_WEBSERVER_BASEURL")
    or os.getenv("WEBSERVER_BASEURL")
    or "http://localhost:9196"
).rstrip("/")
WEBSERVER_BASEURL = _SUPERSET_PUBLIC_URL + "/"

# --- Feature Flags ---
FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
    "GUEST_ROLE_ACCESS": True,
    "EMBEDDABLE_CHARTS": True,
    "DRILL_TO_DETAIL": True,
    "ENABLE_TEMPLATE_PROCESSING": True
}

# ----------------------------------------------------------------------
# EMBEDDING CONFIGURATION
# ----------------------------------------------------------------------
GUEST_ROLE_NAME = os.getenv("GUEST_ROLE_NAME", "EmbedUser")
GUEST_TOKEN_JWT_SECRET = os.getenv('GUEST_TOKEN_JWT_SECRET', '')
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_TOKEN_HEADER_NAME = "X-GuestToken"
GUEST_TOKEN_JWT_EXP_SECONDS = int(os.getenv('GUEST_TOKEN_JWT_EXP_SECONDS', '3600'))

def _to_origin(value: str) -> str:
    raw = (value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw

# Allow iframe embedding from your frontend (XFO is legacy; CSP frame-ancestors is preferred)
HTTP_HEADERS = {"X-Frame-Options": "ALLOWALL"}

# Enable CORS for your frontend domains (read from CORS_ORIGINS env var)
ENABLE_CORS = True
_cors_origins = os.getenv(
    'CORS_ORIGINS',
    'http://localhost:9192,http://localhost:3000,http://localhost:5050',
)
_cors_origin_values = [o.strip() for o in _cors_origins.split(",") if o.strip()]
_cors_has_wildcard = any(o == "*" for o in _cors_origin_values)

# For CSP `frame-ancestors` and embedded dashboard allowlists, `*` is not useful.
# You must provide explicit iframe parent origins.
ALLOWED_EMBEDDED_DOMAINS = [_to_origin(o) for o in _cors_origin_values if o != "*"]
if _cors_has_wildcard:
    print("! CORS_ORIGINS contains `*` (wildcard). For embedding, set explicit origins (no `*`).")
_extra_embed_origins = os.getenv("SUPERSET_EMBED_EXTRA_ORIGINS", "")
if _extra_embed_origins.strip():
    for _o in _extra_embed_origins.split(","):
        _origin = _to_origin(_o)
        if _origin and _origin not in ALLOWED_EMBEDDED_DOMAINS:
            ALLOWED_EMBEDDED_DOMAINS.append(_origin)

_cors_option_origins = ["*"] if _cors_has_wildcard else ALLOWED_EMBEDDED_DOMAINS
CORS_OPTIONS = {
    # When wildcard origins are used, credentials must be disabled (browser restriction).
    "supports_credentials": not _cors_has_wildcard,
    "allow_headers": ["*"],
    "resources": ["*"],
    "origins": _cors_option_origins,
}

# ----------------------------------------------------------------------
# Admin UI authentication (DB-only)
# ----------------------------------------------------------------------
AUTH_TYPE = AUTH_DB
AUTH_USER_REGISTRATION = False

# ----------------------------------------------------------------------
# Session and Security
# ----------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

# Reduce noise / avoid DB-backed event logging issues (LocalProxy warnings) by
# default. If you need DB event logs, set DISABLE_DB_EVENT_LOGGER=0.
DISABLE_DB_EVENT_LOGGER = _env_bool("DISABLE_DB_EVENT_LOGGER", True)
if DISABLE_DB_EVENT_LOGGER:
    try:
        from superset.utils.log import NullEventLogger

        EVENT_LOGGER = NullEventLogger()
        print("✓ DB event logger disabled (NullEventLogger)")
    except Exception as e:
        print(f"! Could not disable DB event logger (continuing): {e}")

# Local/dev escape hatch: CSRF issues can manifest if host/container time jumps backwards
# (e.g., Signature age < 0). Keep CSRF enabled by default.
SUPERSET_DISABLE_CSRF = _env_bool("SUPERSET_DISABLE_CSRF", False)
WTF_CSRF_ENABLED = not SUPERSET_DISABLE_CSRF
 
WTF_CSRF_EXEMPT_LIST = [
    "superset.views.core.log",
    "superset.app.generate_guest_token_handler",
    "superset.app.studio_login_handler",
    "/api/guest-token/generate",
    "api.v1.security.guest_token",
]
if SUPERSET_DISABLE_CSRF:
    WTF_CSRF_EXEMPT_LIST = ["*"]
    WTF_CSRF_TIME_LIMIT = None

TALISMAN_CONFIG = {
    "content_security_policy": {
        "frame-ancestors": ["'self'", *ALLOWED_EMBEDDED_DOMAINS],
    },
    "force_https": False,
    # Disable Talisman's X-Frame-Options so it doesn't conflict with embedding.
    # CSP frame-ancestors is the modern, preferred control.
    "frame_options": None,
    "session_cookie_secure": False,
}

PERMANENT_SESSION_LIFETIME = 1800
# ----------------------------------------------------------------------
# Session and Security - Cookie Overrides
# ----------------------------------------------------------------------
# 'Lax' works for same-site (same host, different ports) over HTTP.
# For HTTPS deployment: keep 'Lax' (same domain) or use 'None' + Secure=True (cross-domain).
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False  # Set to True when using HTTPS in production
SESSION_COOKIE_HTTPONLY = True


print("✓ Superset configuration loaded with embedding enabled")
print(f"✓ WEBSERVER_BASEURL (public): {WEBSERVER_BASEURL}")
print(f"✓ Guest Token JWT Secret: {GUEST_TOKEN_JWT_SECRET[:10]}...")
print(f"✓ Allowed domains: {ALLOWED_EMBEDDED_DOMAINS}")
print("✓ Superset UI auth: AUTH_DB (admin UI only)")
