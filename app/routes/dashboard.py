import sys
from pathlib import Path
from subprocess import TimeoutExpired, run

from flask import Blueprint, jsonify, render_template

from app import repositories as repo
from app.db import get_db

bp = Blueprint("dashboard", __name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE_TEST_SCRIPT = REPO_ROOT / "scripts" / "smoke_test.py"
SMOKE_TEST_TIMEOUT_SECONDS = 60


@bp.get("/")
def dashboard():
    return render_template("dashboard.html")


@bp.get("/api/dashboard/stats")
def dashboard_stats():
    return jsonify(repo.get_dashboard_stats(get_db()))


@bp.post("/api/dashboard/smoke-test")
def run_smoke_test():
    """Runs scripts/smoke_test.py as a subprocess against this same server
    and database, so the dashboard's "run tests" button exercises the real
    end-to-end suite rather than a separate, parallel implementation of it.

    Inherits the app process's environment (DATABASE_URL etc, set by
    docker-compose.yml/config.py) rather than the script's own
    localhost-only defaults, which only make sense when it's run from a
    shell outside the container.
    """
    try:
        result = run(
            [sys.executable, str(SMOKE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=SMOKE_TEST_TIMEOUT_SECONDS,
            cwd=REPO_ROOT,
        )
    except TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return jsonify({"success": False, "output": output + "\n\nTimed out."}), 504

    output = result.stdout + result.stderr
    return jsonify({"success": result.returncode == 0, "exit_code": result.returncode, "output": output})
