#!/bin/bash

# Exit on error
set -e

# --- CRITICAL FIX ---
# Tell Flask where the Superset application is
export FLASK_APP="superset.app:create_app()"
# --------------------

echo "Starting Superset initialization..."

SUPERSET_PORT="${SUPERSET_PORT:-9196}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"

ADMIN_USERNAME="${SUPERSET_ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${SUPERSET_ADMIN_PASSWORD:-admin}"
ADMIN_FIRSTNAME="${SUPERSET_ADMIN_FIRSTNAME:-Admin}"
ADMIN_LASTNAME="${SUPERSET_ADMIN_LASTNAME:-User}"
ADMIN_EMAIL="${SUPERSET_ADMIN_EMAIL:-admin@example.com}"

echo "Upgrading DB (always; required for version upgrades)..."
superset db upgrade

echo "Creating admin user (idempotent)..."
set +e
CREATE_OUT="$(superset fab create-admin \
    --username "${ADMIN_USERNAME}" \
    --firstname "${ADMIN_FIRSTNAME}" \
    --lastname "${ADMIN_LASTNAME}" \
    --email "${ADMIN_EMAIL}" \
    --password "${ADMIN_PASSWORD}" 2>&1)"
CREATE_RC=$?
set -e

if [ $CREATE_RC -ne 0 ]; then
    if echo "${CREATE_OUT}" | grep -qi "already exists"; then
        echo "Admin user '${ADMIN_USERNAME}' already exists."
    else
        echo "${CREATE_OUT}"
        exit $CREATE_RC
    fi
fi

ensure_admin_role() {
    echo "Ensuring admin user '${ADMIN_USERNAME}' has Admin role..."
    set +e
    python - <<'PY'
import os

from superset.app import create_app
from superset.extensions import db

app = create_app()

with app.app_context():
    sm = app.appbuilder.sm
    username = os.environ.get("SUPERSET_ADMIN_USERNAME", "admin")
    user = sm.find_user(username=username)
    if not user:
        print(f"! Admin user '{username}' not found; skipping role ensure")
        raise SystemExit(0)

    role_name = getattr(sm, "auth_role_admin", None) or "Admin"
    role = sm.find_role(role_name) or sm.find_role("Admin")
    if not role:
        print(f"! Admin role '{role_name}' not found; skipping")
        raise SystemExit(0)

    if role in user.roles:
        print(f"✓ User '{username}' already has role '{role.name}'")
        raise SystemExit(0)

    user.roles.append(role)
    db.session.commit()
    print(f"✓ Ensured user '{username}' has role '{role.name}'")
PY
    RC=$?
    set -e
    if [ $RC -ne 0 ]; then
        echo "Warning: failed to ensure Admin role (continuing)."
    fi
}

echo "Initializing Superset (safe on every start)..."
superset init

ensure_admin_role

ensure_embed_role() {
    echo "Ensuring guest role '${GUEST_ROLE_NAME:-EmbedUser}' exists (read-only baseline)..."
    set +e
    python - <<'PY'
import os

from superset.app import create_app
from superset.extensions import db

app = create_app()

with app.app_context():
    sm = app.appbuilder.sm

    target_role_name = os.getenv("GUEST_ROLE_NAME", "EmbedUser").strip() or "EmbedUser"

    target_role = sm.find_role(target_role_name) or sm.add_role(target_role_name)

    # Minimal allowlist: only allow reading Charts and Dashboards.
    # Superset/FAB internals vary: permission name may be `can_read` or `can read`,
    # and view-menu names may be `ChartRestApi`, `DashboardRestApi`, etc.
    candidate_perm_names = {"can_read", "can read"}

    def _pv_info(pv):
        perm = (getattr(getattr(pv, "permission", None), "name", "") or "").strip()
        view = (getattr(getattr(pv, "view_menu", None), "name", "") or "").strip()
        return perm, view

    # Pull all PermissionViews once and match in-memory for robustness.
    pv_rows = db.session.query(sm.permissionview_model).all()

    def _score_chart(view_name_lower: str) -> int:
        if view_name_lower == "chartrestapi":
            return 100
        if view_name_lower == "slicerestapi":
            return 95
        if view_name_lower == "chartmodelview":
            return 90
        if view_name_lower == "slicemodelview":
            return 85
        if view_name_lower == "chart":
            return 80
        if view_name_lower == "slice":
            return 75
        if "chart" in view_name_lower:
            return 50
        if "slice" in view_name_lower:
            return 45
        return 0

    def _score_dashboard(view_name_lower: str) -> int:
        if view_name_lower == "dashboardrestapi":
            return 100
        if view_name_lower == "dashboardmodelview":
            return 90
        if view_name_lower == "dashboard":
            return 80
        if "dashboard" in view_name_lower:
            return 50
        return 0

    def _score_chartdata(view_name_lower: str) -> int:
        if view_name_lower == "chartdatarestapi":
            return 100
        if view_name_lower.startswith("chartdata"):
            return 80
        if "chartdata" in view_name_lower:
            return 60
        return 0

    def _score_dataset(view_name_lower: str) -> int:
        if view_name_lower == "datasetrestapi":
            return 100
        if view_name_lower == "sqlatablemodelview":
            return 90
        if view_name_lower.startswith("dataset"):
            return 80
        if "dataset" in view_name_lower:
            return 40
        return 0

    def _score_explore(view_name_lower: str) -> int:
        if view_name_lower == "explorejsonrestapi":
            return 100
        if view_name_lower.startswith("explore"):
            return 70
        if "explore" in view_name_lower:
            return 50
        return 0

    def _score_embedded_dashboard(view_name_lower: str) -> int:
        # Superset embedded dashboards REST API (names vary by version)
        if view_name_lower == "embeddeddashboardrestapi":
            return 100
        if view_name_lower == "embedded_dashboardrestapi":
            return 98
        if view_name_lower.startswith("embeddeddashboard"):
            return 80
        if view_name_lower.startswith("embedded_dashboard"):
            return 75
        if "embeddeddashboard" in view_name_lower or "embedded_dashboard" in view_name_lower:
            return 50
        return 0

    chart_candidates = []
    dashboard_candidates = []
    chartdata_candidates = []
    dataset_candidates = []
    explore_candidates = []
    embedded_dashboard_candidates = []
    diag = []

    for pv in pv_rows:
        perm_name, view_name = _pv_info(pv)
        perm_name_l = perm_name.lower()
        view_name_l = view_name.lower()

        if perm_name_l not in candidate_perm_names:
            continue

        if any(k in view_name_l for k in ("chart", "slice", "dashboard")):
            diag.append((perm_name, view_name))

        cscore = _score_chart(view_name_l)
        if cscore:
            chart_candidates.append((cscore, pv, perm_name, view_name))

        dscore = _score_dashboard(view_name_l)
        if dscore:
            dashboard_candidates.append((dscore, pv, perm_name, view_name))

        cds = _score_chartdata(view_name_l)
        if cds:
            chartdata_candidates.append((cds, pv, perm_name, view_name))

        dss = _score_dataset(view_name_l)
        if dss:
            dataset_candidates.append((dss, pv, perm_name, view_name))

        es = _score_explore(view_name_l)
        if es:
            explore_candidates.append((es, pv, perm_name, view_name))

        eds = _score_embedded_dashboard(view_name_l)
        if eds:
            embedded_dashboard_candidates.append((eds, pv, perm_name, view_name))

    chart_candidates.sort(key=lambda t: t[0], reverse=True)
    dashboard_candidates.sort(key=lambda t: t[0], reverse=True)
    chartdata_candidates.sort(key=lambda t: t[0], reverse=True)
    dataset_candidates.sort(key=lambda t: t[0], reverse=True)
    explore_candidates.sort(key=lambda t: t[0], reverse=True)
    embedded_dashboard_candidates.sort(key=lambda t: t[0], reverse=True)

    chart_pv = chart_candidates[0][1] if chart_candidates else None
    dashboard_pv = dashboard_candidates[0][1] if dashboard_candidates else None
    chartdata_pv = chartdata_candidates[0][1] if chartdata_candidates else None
    dataset_pv = dataset_candidates[0][1] if dataset_candidates else None
    explore_pv = explore_candidates[0][1] if explore_candidates else None
    embedded_dashboard_pv = embedded_dashboard_candidates[0][1] if embedded_dashboard_candidates else None

    if chart_pv and dashboard_pv:
        pvs = [chart_pv, dashboard_pv]
        if embedded_dashboard_pv:
            pvs.append(embedded_dashboard_pv)
        if chartdata_pv:
            pvs.append(chartdata_pv)
        if dataset_pv:
            pvs.append(dataset_pv)
        if explore_pv:
            pvs.append(explore_pv)

        # De-dup while preserving order
        unique_pvs = []
        seen = set()
        for pv in pvs:
            pv_id = getattr(pv, "id", None) or (id(pv))
            if pv_id in seen:
                continue
            seen.add(pv_id)
            unique_pvs.append(pv)

        for pv in unique_pvs:
            perm, view = _pv_info(pv)
            print(f"✓ Using PermissionView: {perm} on {view}")

        target_role.permissions = unique_pvs
        db.session.commit()
        print(f"✓ Ensured role '{target_role_name}' exists with {len(unique_pvs)} permission views")
    else:
        # Do not overwrite existing perms if we cannot resolve both.
        diag_sorted = sorted(set(diag), key=lambda t: (t[0].lower(), t[1].lower()))
        print(
            f"! Could not resolve required PermissionViews (chart={bool(chart_pv)} dashboard={bool(dashboard_pv)})."
        )
        print(f"! Leaving role '{target_role_name}' permissions unchanged.")
        if diag_sorted:
            print("! Available matching PermissionViews (perm, view_menu):")
            for perm_name, view_name in diag_sorted:
                print(f"  - {perm_name} :: {view_name}")
PY
    RC=$?
    set -e
    if [ $RC -ne 0 ]; then
        echo "Warning: failed to ensure EmbedUser role (continuing)."
    fi
}

ensure_embed_role

ensure_embedded_dashboards() {
    echo "Ensuring embedded dashboards are enabled (and allowed domains set when empty)..."
    set +e
    python - <<'PY'
import os
from urllib.parse import urlparse

from superset.app import create_app
from superset.extensions import db


def _to_origin(value: str) -> str:
    raw = (value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw


def _import_embedded_dashboard_model():
    candidates = (
        "superset.models.embedded_dashboard",
        "superset.models.embedded",
    )
    for mod_name in candidates:
        try:
            module = __import__(mod_name, fromlist=["EmbeddedDashboard"])
            model = getattr(module, "EmbeddedDashboard", None)
            if model is not None:
                return model
        except Exception:
            continue
    return None


app = create_app()

with app.app_context():
    EmbeddedDashboard = _import_embedded_dashboard_model()
    if EmbeddedDashboard is None:
        print("! EmbeddedDashboard model not found; skipping")
        raise SystemExit(0)

    cors_env = os.getenv("CORS_ORIGINS", "")
    raw_values = [o.strip() for o in cors_env.split(",") if o.strip()]
    has_wildcard = "*" in raw_values
    origins = [_to_origin(o) for o in raw_values if o != "*"]
    origins = [o for o in origins if o]

    auto_allow = os.getenv("SUPERSET_EMBED_AUTO_ALLOW_ORIGINS", "1").strip().lower() not in {"0", "false", "no"}
    if auto_allow and has_wildcard and not origins:
        print("! CORS_ORIGINS contains `*` (wildcard). Set explicit origins to auto-fill EmbeddedDashboard.allowed_domains.")

    rows = db.session.query(EmbeddedDashboard).all()
    updated_rows = 0

    for row in rows:
        changed = False

        if hasattr(row, "enabled") and getattr(row, "enabled") is None:
            setattr(row, "enabled", True)
            changed = True

        if auto_allow and origins and hasattr(row, "allowed_domains"):
            allowed = getattr(row, "allowed_domains", None)
            if not allowed:
                # `allowed_domains` can be a JSON/list field depending on Superset version.
                # If it's a string column, fall back to a comma-separated string.
                if isinstance(allowed, str):
                    setattr(row, "allowed_domains", ",".join(origins))
                else:
                    setattr(row, "allowed_domains", origins)
                changed = True

        if changed:
            updated_rows += 1

    if updated_rows:
        db.session.commit()
        print(f"✓ Updated {updated_rows} embedded dashboard record(s)")
    else:
        print("✓ Embedded dashboards already look enabled/allowlisted")
PY
    RC=$?
    set -e
    if [ $RC -ne 0 ]; then
        echo "Warning: failed to ensure embedded dashboards are enabled (continuing)."
    fi
}

ensure_embedded_dashboards

echo "Starting Superset server on port $SUPERSET_PORT..."
# Start the production server
gunicorn \
    --bind  "0.0.0.0:$SUPERSET_PORT" \
    --workers "${GUNICORN_WORKERS}" \
    --timeout "${GUNICORN_TIMEOUT}" \
    --limit-request-line 0 \
    --limit-request-field_size 0 \
    "superset.app:create_app()"
