"""scripts/install_user_units.sh: the toolbox's background jobs as user units (no sudo)."""

import os
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


def test_user_units_with_failure_alerts_and_a_health_list(tmp_path):
    dest, cfg = tmp_path / "units", tmp_path / "cfg"
    dest.mkdir()
    (dest / "ai-newsroom-west.service").write_text("[Service]\nExecStart=/bin/true\n")  # a plugin's unit
    env = {**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(cfg), "RUNTIME_DIR": str(tmp_path / "rt")}
    subprocess.run(
        ["bash", str(KIT / "scripts" / "install_user_units.sh"), "--dest", str(dest)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    for name in (
        "ai-scheduler.service",
        "ai-scheduler.timer",
        "ai-files.service",
        "ai-knowledge.timer",
        "ai-model-preload.service",
        "ai-warmup.timer",
        "ai-health-check.timer",
        "ai-notify-failure@.service",
    ):
        text = (dest / name).read_text()
        assert "User=" not in text and "multi-user.target" not in text and "sudo" not in text, name
    assert "--user --unit %i" in (dest / "ai-notify-failure@.service").read_text()
    assert (
        "OnFailure=ai-notify-failure@%n.service" in (dest / "ai-newsroom-west.service.d" / "onfailure.conf").read_text()
    )
    assert not (dest / "ai-notify-failure@.service.d").exists()
    health = (cfg / "ai-alerts" / "health.env").read_text()
    assert "user ai-newsroom-west.service 26" in health and "ai-mentor" not in health
    assert f"Environment=WORKSPACE={os.environ['WORKSPACE']}" in (dest / "ai-files.service").read_text()
