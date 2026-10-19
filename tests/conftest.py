"""Every test runs as on a fresh install: your own settings (config/models.env) and the
background-job queue of a running Dhoon never change a test's result, and a test never waits
while you chat."""

import pytest


@pytest.fixture(autouse=True)
def fresh_install(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELS_ENV", str(tmp_path / "no-models.env"))
    monkeypatch.setenv("HEAVY_DIR", str(tmp_path / "heavy"))
