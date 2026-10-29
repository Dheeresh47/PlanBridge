"""
Guards on how the application resolves its environment.

These exist because the module-level engine and the working-directory
relative env file they replace were a standing risk of connecting to the
wrong database.
"""

import os
import subprocess
import sys

from app.config import BACKEND_DIR, DEFAULT_ENV_FILE


REPO_ROOT = BACKEND_DIR.parent


def test_env_file_is_resolved_from_the_package_not_the_working_directory():
    assert DEFAULT_ENV_FILE.is_absolute()
    assert DEFAULT_ENV_FILE == BACKEND_DIR / ".env"


def test_importing_the_application_creates_no_engine_or_settings():
    """Importing app must not read the environment or connect anywhere."""
    script = (
        "import app.models, app.main\n"
        "from app.config import get_settings\n"
        "from app.database import get_engine, get_sessionmaker\n"
        "print(get_settings.cache_info().currsize,"
        " get_engine.cache_info().currsize,"
        " get_sessionmaker.cache_info().currsize)\n"
    )

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(BACKEND_DIR)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.split() == ["0", "0", "0"]


def test_database_module_exposes_no_import_time_engine():
    import app.database as database

    assert not hasattr(database, "engine")
    assert not hasattr(database, "SessionLocal")
