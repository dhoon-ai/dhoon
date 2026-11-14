"""Every test runs as on a fresh install: your own settings (config/models.env) and the
background-job queue of a running Dhoon never change a test's result, and a test never waits
while you chat."""

import pytest


@pytest.fixture(autouse=True)
def fresh_install(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELS_ENV", str(tmp_path / "no-models.env"))
    monkeypatch.setenv("HEAVY_DIR", str(tmp_path / "heavy"))
    # the real paths file and its variables (exported by ~/.profile) never reach a test
    monkeypatch.setenv("PATHS_ENV", str(tmp_path / "no-paths.env"))
    for name in ("PROJECT_DIR", "AGENT_DIR", "MODELS_DIR", "BACKUP_USB", "DHOON_HOME", "DHOON_DEV", "BLOCKS_HOME",
                 "BLOCKS_DEV", "SITE_DIR", "KNOWREVA_SITE", "PAIRDESK_DIR", "WORKSPACE", "WORKSPACE_DIR", "BEAM_QUEUE",
                 "CONTAINER_DATA", "CONTAINER_STORAGE", "CONFIG_DIR", "VENV", "BACKUP_CONF", "RESTORE_DIR", "RESTIC_ENV"):
        monkeypatch.delenv(name, raising=False)
