import os
import importlib
import secrets
import uuid
from superset.security import SupersetSecurityManager
from flask import request, jsonify, current_app, g, redirect, make_response
import jwt as pyjwt
from flask_login import login_user
import logging

logger = logging.getLogger(__name__)

# CORS allowed origins — read from CORS_ORIGINS env var
_cors_env = os.getenv('CORS_ORIGINS', 'http://localhost:9192,http://localhost:3000')
ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(',') if o.strip()]

def add_cors_headers(response, origin=None, supports_credentials: bool = True):
    """Add CORS headers to response"""
    # If `*` is configured, reflect the request origin so browsers can use
    # credentials (wildcard + credentials is rejected by browsers).
    if origin and (origin in ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    else:
        # Fallback to first allowed origin (or reflect origin if provided)
        response.headers["Access-Control-Allow-Origin"] = (
            ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else (origin or "*")
        )
        if origin:
            response.headers["Vary"] = "Origin"
    
    if supports_credentials:
        response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers['Access-Control-Allow-Headers'] = (
        'Content-Type, Authorization, X-Requested-With, Accept, X-API-Key, X-GuestToken'
    )
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Max-Age'] = '3600'
    return response

def cors_preflight_response(origin=None, supports_credentials: bool = True):
    """Create a response for CORS preflight OPTIONS requests"""
    response = make_response('', 200)
    return add_cors_headers(response, origin, supports_credentials=supports_credentials)


class CustomSecurityManager(SupersetSecurityManager):
    
    def __init__(self, appbuilder):
        super(CustomSecurityManager, self).__init__(appbuilder)
        KEYCLOAK_PUBLIC_KEY = os.getenv('KEYCLOAK_PUBLIC_KEY', 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApIge3w9G3JiMGshGKNpOyjTeuAFBRacetlP7V9FE4AYEKxz504BrwhjI5LKNttZghOFicEs/tVsdeESM/pqkBkrTqWb7wAsbx7DUAhEB6FhAl5yZlC0bfo+2dcYgELNFWtG5ZakEHiOwjvZpmd6jTUEPjdbno0AsiAvbji27DM2wqUGBVy3Uotw6bOaB92tTbqntF86ZoIY0jHi41jHe3VOjbz3mdDRCsD/vvtErLvsySKxkfAk/ZiiHy0oDwpcX2ouemE85gPuZ9q/g21QHUdJPip655eDQ2mDRnXdf4GW8UlkvqYWHI6wX0jwIA96O30LudkNqk0wCNJEEvCRDFQIDAQAB')
        self.keycloak_pub_key = f"""-----BEGIN PUBLIC KEY-----
{KEYCLOAK_PUBLIC_KEY}
-----END PUBLIC KEY-----"""
        # Register endpoints after app is fully initialized
        self._endpoints_registered = False
        self._register_endpoints()

    def _get_flask_app(self):
        """
        Superset/Flask-AppBuilder compatibility shim.

        - Superset 5: AppBuilder exposed `get_app` (callable or property)
        - Superset 6: AppBuilder commonly exposes `app`
        """

        ab = self.appbuilder

        if hasattr(ab, "get_app"):
            get_app = getattr(ab, "get_app")
            return get_app() if callable(get_app) else get_app

        if hasattr(ab, "app"):
            return getattr(ab, "app")

        raise RuntimeError("Cannot locate Flask app from AppBuilder")

    def _patch_csrf_for_guest_token_header(self, app) -> None:
        """
        Keep CSRF enabled for admin cookie/session traffic, but bypass CSRF for
        embedded/guest-token API calls authenticated via the guest token header.

        This prevents CSRF failures for embedded requests that carry the
        `X-GuestToken` header (Superset embedded SDK behavior).
        """

        csrf = app.extensions.get("csrf")
        if not csrf:
            return

        if getattr(csrf, "_guesttoken_csrf_patched", False):
            return

        header_name = app.config.get("GUEST_TOKEN_HEADER_NAME", "X-GuestToken")
        orig_protect = csrf.protect

        def protect():
            try:
                guest_token = request.headers.get(header_name, "")
                if guest_token:
                    return None
            except Exception:
                pass
            return orig_protect()

        csrf.protect = protect
        setattr(csrf, "_guesttoken_csrf_patched", True)

    def _register_endpoints(self):
        """Register custom endpoints - called when app context is available"""
        if self._endpoints_registered:
            return
        
        try:
            app = self._get_flask_app()
            self._patch_csrf_for_guest_token_header(app)
            self._setup_embed_path_redirect(app)
            self._setup_guest_token_endpoint(app)
            self._setup_studio_endpoint(app)
            self._endpoints_registered = True
        except Exception as e:
            logger.error(f"Failed to register custom endpoints: {e}")
            print(f"✗ Failed to register custom endpoints: {e}")

    def _setup_embed_path_redirect(self, app) -> None:
        """
        Compatibility layer for Superset embed URL differences.

        The official `@superset-ui/embedded-sdk` iframes `${SUPERSET}/embedded/<id>`.
        Some deployments expose the embedded dashboard page at `/embedded/dashboard/<id>`.

        Configure via env `SUPERSET_EMBED_PATH_STYLE`:
          - auto (default): redirect only if `/embedded/dashboard/` route exists
          - dashboard: always redirect `/embedded/<uuid>` -> `/embedded/dashboard/<uuid>`
          - plain: never redirect
        """

        if getattr(app, "_superset_embed_redirect_registered", False):
            return

        style = (os.getenv("SUPERSET_EMBED_PATH_STYLE") or "auto").strip().lower()

        def _has_dashboard_embed_route() -> bool:
            try:
                for rule in app.url_map.iter_rules():
                    if str(getattr(rule, "rule", "")).startswith("/embedded/dashboard/"):
                        return True
            except Exception:
                return False
            return False

        @app.before_request
        def _embedded_path_redirect_hook():
            try:
                if request.method not in ("GET", "HEAD"):
                    return None

                path = (request.path or "").strip()
                if not path.startswith("/embedded/"):
                    return None

                # Never redirect the dashboard-style route.
                if path.startswith("/embedded/dashboard/"):
                    return None

                # Match exactly: /embedded/<uuid>
                parts = path.split("/")
                if len(parts) != 3 or parts[1] != "embedded" or not parts[2]:
                    return None

                try:
                    embedded_uuid = uuid.UUID(parts[2])
                except Exception:
                    return None

                effective_style = style
                if effective_style == "auto":
                    effective_style = "dashboard" if _has_dashboard_embed_route() else "plain"

                if effective_style != "dashboard":
                    return None

                location = f"/embedded/dashboard/{embedded_uuid}"
                if request.query_string:
                    location = f"{location}?{request.query_string.decode('utf-8', 'ignore')}"

                return redirect(location, code=302)
            except Exception:
                return None

        setattr(app, "_superset_embed_redirect_registered", True)

    def _setup_guest_token_endpoint(self, app):
        """Setup API endpoints for generating guest tokens"""
        security_manager = self  # Capture self for the closure
        
        def generate_guest_token_handler():
            origin = request.headers.get('Origin')
            
            # Handle CORS preflight
            if request.method == 'OPTIONS':
                return cors_preflight_response(origin, supports_credentials=False)

            # If the caller has an active Superset session, allow minting based
            # on the session user (admin UI usage).
            session_user = None
            if g.user and not g.user.is_anonymous:
                session_user = g.user

            data = request.get_json(silent=True) or {}
            resources = data.get("resources")
            if resources is None:
                response = jsonify({"error": "resources array is required"})
                return add_cors_headers(response, origin, supports_credentials=False), 400

            if not isinstance(resources, list) or len(resources) < 1:
                response = jsonify({"error": "resources must be a non-empty array"})
                return add_cors_headers(response, origin, supports_credentials=False), 400

            # Resolve auth mode (priority: session -> api key -> keycloak bearer)
            auth_mode = "session" if session_user is not None else "none"
            decoded_token = None

            if session_user is None:
                api_key = (request.headers.get("X-API-Key") or "").strip()
                if api_key:
                    expected = (os.getenv("SUPERSET_EMBED_API_KEY") or "").strip()
                    if not expected:
                        response = jsonify({"error": "API key authentication not configured on server"})
                        return add_cors_headers(response, origin, supports_credentials=False), 500

                    if not secrets.compare_digest(api_key, expected):
                        response = jsonify({"error": "Invalid API key"})
                        return add_cors_headers(response, origin, supports_credentials=False), 401

                    auth_mode = "api_key"
                else:
                    auth_header = request.headers.get("Authorization", "")
                    if not auth_header.startswith("Bearer "):
                        response = jsonify({"error": "Authorization Bearer token is required"})
                        return add_cors_headers(response, origin, supports_credentials=False), 401

                    token = auth_header.split(" ", 1)[1].strip()
                    if not token:
                        response = jsonify({"error": "Authorization Bearer token is required"})
                        return add_cors_headers(response, origin, supports_credentials=False), 401

                    try:
                        decoded_token = pyjwt.decode(
                            token,
                            security_manager.keycloak_pub_key,
                            algorithms=["RS256"],
                            options={"verify_aud": False},
                        )
                        auth_mode = "keycloak"
                    except pyjwt.ExpiredSignatureError:
                        response = jsonify({"error": "Token has expired"})
                        return add_cors_headers(response, origin, supports_credentials=False), 401
                    except pyjwt.InvalidTokenError as e:
                        response = jsonify({"error": f"Invalid token: {str(e)}"})
                        return add_cors_headers(response, origin, supports_credentials=False), 401
                    except Exception as e:
                        response = jsonify({"error": f"Token decode failed: {str(e)}"})
                        return add_cors_headers(response, origin, supports_credentials=False), 401

            # Validate resources and ensure embedded dashboards exist
            try:
                from superset.extensions import db
            except Exception as e:
                response = jsonify({"error": f"Server error: cannot load DB session: {str(e)}"})
                return add_cors_headers(response, origin, supports_credentials=False), 500

            EmbeddedDashboard = None
            embedded_import_error = None
            for mod_name in (
                "superset.models.embedded_dashboard",
                "superset.models.embedded",
            ):
                try:
                    module = importlib.import_module(mod_name)
                    EmbeddedDashboard = getattr(module, "EmbeddedDashboard", None)
                    if EmbeddedDashboard is not None:
                        break
                except Exception as e:
                    embedded_import_error = e

            Dashboard = None
            try:
                from superset.models.dashboard import Dashboard as _Dashboard

                Dashboard = _Dashboard
            except Exception:
                Dashboard = None

            # Superset guest tokens are scoped to *Dashboard.uuid* resources.
            # The Embedded SDK "id" is typically EmbeddedDashboard.uuid. We translate
            # embedded UUIDs -> underlying Dashboard.uuid for the guest token.
            scoped_resources = []
            scoped_resource_ids = set()

            def _push_resource(value: str) -> None:
                raw = (value or "").strip()
                if not raw or raw in scoped_resource_ids:
                    return
                scoped_resource_ids.add(raw)
                scoped_resources.append({"type": "dashboard", "id": raw})

            for idx, res in enumerate(resources):
                if not isinstance(res, dict):
                    response = jsonify({"error": f"resources[{idx}] must be an object"})
                    return add_cors_headers(response, origin, supports_credentials=False), 400

                if (res.get("type") or "").strip() != "dashboard":
                    response = jsonify({"error": "Only dashboard resources are supported"})
                    return add_cors_headers(response, origin, supports_credentials=False), 400

                dashboard_id = (res.get("id") or "").strip()
                if not dashboard_id:
                    response = jsonify({"error": f"resources[{idx}].id is required"})
                    return add_cors_headers(response, origin, supports_credentials=False), 400

                try:
                    embedded_uuid = uuid.UUID(dashboard_id)
                except Exception:
                    response = jsonify({"error": f"resources[{idx}].id must be an embedded dashboard UUID"})
                    return add_cors_headers(response, origin, supports_credentials=False), 400

                embedded_record = None
                if EmbeddedDashboard is not None:
                    for field_name, value in (
                        ("uuid", embedded_uuid),
                        ("uuid", str(embedded_uuid)),
                        ("id", embedded_uuid),
                        ("id", str(embedded_uuid)),
                    ):
                        if not hasattr(EmbeddedDashboard, field_name):
                            continue
                        try:
                            embedded_record = (
                                db.session.query(EmbeddedDashboard)
                                .filter(getattr(EmbeddedDashboard, field_name) == value)
                                .one_or_none()
                            )
                            if embedded_record is not None:
                                break
                        except Exception:
                            continue

                if embedded_record is not None:
                    if hasattr(embedded_record, "enabled") and getattr(embedded_record, "enabled") is False:
                        response = jsonify(
                            {
                                "error": f"Embedded dashboard not found (disabled): {embedded_uuid}",
                            }
                        )
                        return add_cors_headers(response, origin, supports_credentials=False), 404

                    # Compatibility: some deployments scope guest tokens to the embedded UUID.
                    _push_resource(str(embedded_uuid))

                    # Resolve underlying dashboard UUID
                    dashboard_uuid = None
                    try:
                        if hasattr(embedded_record, "dashboard") and getattr(embedded_record, "dashboard") is not None:
                            dash_obj = getattr(embedded_record, "dashboard")
                            if hasattr(dash_obj, "uuid"):
                                dashboard_uuid = getattr(dash_obj, "uuid")
                        if dashboard_uuid is None and Dashboard is not None and hasattr(embedded_record, "dashboard_id"):
                            dash_id = getattr(embedded_record, "dashboard_id")
                            if dash_id:
                                dash_row = (
                                    db.session.query(Dashboard)
                                    .filter(Dashboard.id == dash_id)
                                    .one_or_none()
                                )
                                if dash_row is not None:
                                    dashboard_uuid = getattr(dash_row, "uuid", None)
                    except Exception:
                        dashboard_uuid = None

                    if not dashboard_uuid:
                        response = jsonify(
                            {
                                "error": f"Embedded dashboard is missing an underlying dashboard mapping: {embedded_uuid}",
                            }
                        )
                        return add_cors_headers(response, origin, supports_credentials=False), 500

                    _push_resource(str(dashboard_uuid))
                    continue

                # Compatibility fallback: allow passing a raw Dashboard.uuid when embedded dashboards
                # are not used / not available.
                dashboard = None
                if Dashboard is not None:
                    try:
                        dashboard = (
                            db.session.query(Dashboard)
                            .filter(Dashboard.uuid == embedded_uuid)
                            .one_or_none()
                        )
                    except Exception:
                        dashboard = None

                    if dashboard is None:
                        try:
                            dashboard = (
                                db.session.query(Dashboard)
                                .filter(Dashboard.uuid == str(embedded_uuid))
                                .one_or_none()
                            )
                        except Exception:
                            dashboard = None

                if dashboard is None:
                    if EmbeddedDashboard is None and embedded_import_error is not None:
                        response = jsonify(
                            {
                                "error": "Embedded dashboard model not available on server; enable embedding in Superset UI",
                            }
                        )
                        return add_cors_headers(response, origin, supports_credentials=False), 500

                    response = jsonify(
                        {
                            "error": f"Embedded dashboard not found (enable embedding in Superset UI): {embedded_uuid}",
                        }
                    )
                    return add_cors_headers(response, origin, supports_credentials=False), 404

                # When a raw Dashboard.uuid is provided, scope the guest token to that dashboard UUID.
                dashboard_uuid_value = getattr(dashboard, "uuid", None) if dashboard is not None else None
                if not dashboard_uuid_value:
                    response = jsonify({"error": f"Dashboard UUID could not be resolved: {embedded_uuid}"})
                    return add_cors_headers(response, origin, supports_credentials=False), 500
                # Compatibility: include both IDs (often equal in this fallback path).
                _push_resource(str(embedded_uuid))
                _push_resource(str(dashboard_uuid_value))

            # Build the guest user payload.
            if session_user is not None:
                guest_user = {
                    "username": session_user.username,
                    "first_name": session_user.first_name or "",
                    "last_name": session_user.last_name or "",
                    "email": session_user.email or "",
                }
            elif auth_mode == "api_key":
                guest_user = {
                    "username": "api_key_user",
                    "first_name": "API",
                    "last_name": "Key",
                    "email": "",
                }
            else:
                username = (
                    decoded_token.get("preferred_username")
                    or decoded_token.get("username")
                    or decoded_token.get("sub")
                )
                if not username:
                    response = jsonify({"error": "Token missing username (preferred_username/sub)"})
                    return add_cors_headers(response, origin, supports_credentials=False), 401

                guest_user = {
                    "username": username,
                    "first_name": decoded_token.get("given_name", "") or "",
                    "last_name": decoded_token.get("family_name", "") or "",
                    "email": decoded_token.get("email", "") or "",
                }

            try:
                guest_token = security_manager.create_guest_access_token(
                    user=guest_user,
                    resources=scoped_resources,
                    rls=[],
                )
                response = jsonify(
                    {
                        "guest_token": guest_token,
                        "expires_in": current_app.config.get(
                            "GUEST_TOKEN_JWT_EXP_SECONDS", 3600
                        ),
                    }
                )
                return add_cors_headers(response, origin, supports_credentials=False)
            except Exception as e:
                response = jsonify({"error": str(e)})
                return add_cors_headers(response, origin, supports_credentials=False), 500
        
        # Exempt from CSRF
        csrf = app.extensions.get("csrf")
        if csrf:
            csrf.exempt(generate_guest_token_handler)

        app.add_url_rule(
            '/api/guest-token/generate',
            endpoint='generate_guest_token_handler',
            view_func=generate_guest_token_handler,
            methods=['POST', 'OPTIONS']
        )
        print("✓ Guest token endpoint registered and CSRF exempted")

    def _setup_studio_endpoint(self, app):
        """Setup studio login endpoint for session-based iframe authentication"""
        security_manager = self  # Capture self for the closure
        
        def studio_login_handler():
            """
            SSO bridge endpoint for iframe-based dashboard embedding.
            
            Accepts a Keycloak JWT via query parameter or Authorization header,
            validates it, creates a Flask session, and redirects to the dashboard.
            
            Query params:
                token    - Keycloak JWT (preferred for iframe src usage)
                redirect - Post-login destination path (default: /dashboard/list/)
            """
            origin = request.headers.get('Origin')
            
            # Handle CORS preflight
            if request.method == 'OPTIONS':
                print(f"Studio login: Handling OPTIONS preflight from {origin}")
                return cors_preflight_response(origin)
            
            # --- 1. Extract token: query param first, then Authorization header ---
            token = request.args.get('token')
            if not token:
                auth_header = request.headers.get("Authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header.split(" ")[1]
            
            if not token:
                print("Studio login: No token provided (query param or header)")
                return "<h3>Authentication required</h3><p>No token provided.</p>", 401
            
            # --- 2. Extract and validate redirect path ---
            redirect_path = request.args.get('redirect', '/dashboard/list/')
            # Security: prevent open redirect — only allow relative paths
            if not redirect_path.startswith('/') or redirect_path.startswith('//') or '://' in redirect_path:
                redirect_path = '/dashboard/list/'
            
            try:
                print(f"Studio login: Attempting to decode token...")
                decoded_token = pyjwt.decode(
                    token, 
                    security_manager.keycloak_pub_key, 
                    algorithms=["RS256"],
                    options={"verify_aud": False}
                )

                realm_access = decoded_token.get("realm_access", {}) or {}
                roles = realm_access.get("roles", []) or []
                if not isinstance(roles, list):
                    roles = []
                roles_lower = {str(r).lower() for r in roles}

                if "admin" not in roles_lower and "super_admin" not in roles_lower:
                    print("Studio login: Forbidden (not an admin role)")
                    return "<h3>Forbidden</h3><p>Admin role required.</p>", 403
                
                username = (
                    decoded_token.get("preferred_username")
                    or decoded_token.get("username")
                    or decoded_token.get("sub")
                )
                print(f"Studio login: Decoded username: {username}")
                
                if not username:
                    print("Studio login: No username in token")
                    return "<h3>Invalid token</h3><p>No username found in token.</p>", 401

                user = security_manager.appbuilder.sm.find_user(username=username)
                
                if not user:
                    print(f"Studio login: User '{username}' not found, auto-creating...")
                    try:
                        role = security_manager.find_role("Admin") or security_manager.find_role("Alpha")
                        if not role:
                            return "<h3>Role missing</h3><p>Admin role not found in Superset.</p>", 500

                        user = security_manager.appbuilder.sm.add_user(
                            username=username,
                            first_name=decoded_token.get("given_name", username) or username,
                            last_name=decoded_token.get("family_name", "") or "",
                            email=decoded_token.get("email", f"{username}@keycloak.local"),
                            role=role,
                        )
                        if not user:
                            print(f"Studio login: Failed to auto-create user '{username}'")
                            return "<h3>User creation failed</h3><p>Could not create user in Superset.</p>", 500
                        print(f"Studio login: Auto-created user '{username}' in Superset")
                    except Exception as create_err:
                        print(f"Studio login: Error auto-creating user: {create_err}")
                        logger.exception(f"Failed to auto-create user {username}")
                        return f"<h3>User creation error</h3><p>{str(create_err)}</p>", 500
                
                # Unwrap proxy object if necessary
                if hasattr(user, "_get_current_object"):
                    user = user._get_current_object()
                
                print(f"Studio login: Found user {user.username}, logging in...")
                
                # Log the user in — this creates the Flask session cookie
                login_user(user, remember=False)
                
                # Always redirect to the dashboard (cookie is set in this response)
                print(f"Studio login: Redirecting {username} to {redirect_path}")
                return redirect(redirect_path)

            except pyjwt.ExpiredSignatureError:
                print("Studio login: Token has expired")
                return "<h3>Session expired</h3><p>Your authentication token has expired. Please refresh the page.</p>", 401
            except pyjwt.InvalidTokenError as e:
                print(f"Studio login: Invalid token - {str(e)}")
                return f"<h3>Invalid token</h3><p>{str(e)}</p>", 401
            except Exception as e:
                print(f"Studio login: Unexpected error - {str(e)}")
                logger.exception("Studio login failed")
                return f"<h3>Login failed</h3><p>{str(e)}</p>", 500

        # Exempt from CSRF
        csrf = app.extensions.get("csrf")
        if csrf:
            csrf.exempt(studio_login_handler)
            
        app.add_url_rule(
            '/api/studio-login',
            endpoint='studio_login_handler',
            view_func=studio_login_handler,
            methods=['GET', 'OPTIONS']  # OPTIONS for CORS preflight
        )
        print("✓ Studio login endpoint registered")

    def oauth_user_info(self, provider, response=None):
        """Extract user info from the Keycloak OIDC token during OAuth login flow.
        
        Superset calls this during the OAuth callback to get user details
        (username, email, first/last name, roles) from the identity provider.
        """
        if provider == 'keycloak':
            try:
                # The access token from the OAuth response
                token = response.get('access_token')
                if not token:
                    logger.error("oauth_user_info: No access_token in OAuth response")
                    return {}

                decoded = pyjwt.decode(
                    token,
                    self.keycloak_pub_key,
                    algorithms=["RS256"],
                    options={"verify_aud": False}
                )

                username = decoded.get("preferred_username", "")
                email = decoded.get("email", "")
                first_name = decoded.get("given_name", "")
                last_name = decoded.get("family_name", "")

                # Extract realm roles for role mapping
                realm_access = decoded.get("realm_access", {})
                roles = realm_access.get("roles", [])

                logger.info(f"oauth_user_info: user={username}, roles={roles}")

                return {
                    "username": username,
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "role_keys": roles,
                }
            except Exception as e:
                logger.exception(f"oauth_user_info: Failed to decode token: {e}")
                return {}
        return {}

    def _resolve_superset_role(self, keycloak_roles):
        """Map Keycloak realm roles to a Superset role object.
        
        Uses the AUTH_ROLES_MAPPING from superset_config.py to determine
        the appropriate Superset role based on Keycloak roles.
        Priority order: super_admin > admin > risk_analyst > analyst > Gamma (default)
        """
        role_mapping = current_app.config.get("AUTH_ROLES_MAPPING", {})
        roles_lower = {r.lower() for r in keycloak_roles}

        # Check in priority order
        for kc_role in ['super_admin', 'admin', 'risk_analyst', 'risk_manager', 'bank_manager', 'analyst']:
            if kc_role in roles_lower and kc_role in role_mapping:
                superset_role_names = role_mapping[kc_role]
                # Return the first matching Superset role that exists
                for role_name in superset_role_names:
                    role = self.find_role(role_name)
                    if role:
                        return role

        # Fallback to default registration role
        default_role_name = current_app.config.get("AUTH_USER_REGISTRATION_ROLE", "Gamma")
        return self.find_role(default_role_name)

    def _auto_create_user(self, decoded_token):
        """Auto-create a Superset user from a decoded Keycloak token.
        
        Used by the studio-login endpoint when a user exists in Keycloak
        but hasn't logged into Superset before.
        """
        username = decoded_token.get("preferred_username") or decoded_token.get("username")
        email = decoded_token.get("email", f"{username}@keycloak.local")
        first_name = decoded_token.get("given_name", username)
        last_name = decoded_token.get("family_name", "")

        # Determine role from Keycloak token
        realm_access = decoded_token.get("realm_access", {})
        keycloak_roles = realm_access.get("roles", [])
        superset_role = self._resolve_superset_role(keycloak_roles)

        if not superset_role:
            superset_role = self.find_role("Gamma")

        logger.info(f"Auto-creating Superset user: {username} with role: {superset_role}")

        user = self.appbuilder.sm.add_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            role=superset_role,
        )
        return user

    def sync_custom_roles(self):
        # Placeholder for custom role synchronization
        pass
