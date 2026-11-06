"""scripts/backup_restic.sh + restore_restic.sh with a stand-in podman that records each call."""

import datetime as dt
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = str(ROOT / "scripts" / "backup_restic.sh")
RESTORE = str(ROOT / "scripts" / "restore_restic.sh")

FAKE = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PODMAN_LOG"
args=" $* "
case "$args" in
  *" images "*) exit 0 ;;
esac
repo=""
prev=""
for a in "$@"; do
  [ "$prev" = "-e" ] && case "$a" in RESTIC_REPOSITORY=*) repo="${a#RESTIC_REPOSITORY=}" ;; esac
  prev="$a"
done
marker="$FAKE_STATE/$(printf '%s' "$repo" | tr '/:' '__')"
case "$args" in
  *" cat config "*) [ -f "$marker" ] ;;
  *" init "*) touch "$marker" ;;
  *" backup "*) [ "$repo" = /repo ] && [ -n "${FAIL_USB:-}" ] && exit 1; exit 0 ;;
  *" restore "*)
    out=""; prev=""
    for a in "$@"; do [ "$prev" = "-v" ] && case "$a" in *:/out) out="${a%:/out}" ;; esac; prev="$a"; done
    mkdir -p "$out" && echo data > "$out/file.txt" ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    conf = home / ".config/ai-kit-backup"
    conf.mkdir(parents=True)
    (conf / "restic-password").write_text("pw\n")
    fake = tmp_path / "podman"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    (tmp_path / "state").mkdir()
    ws, q = tmp_path / "ws", tmp_path / "queue"
    ws.mkdir()
    (ws / "a.txt").write_text("x")
    q.mkdir()
    e = dict(os.environ, HOME=str(home), PODMAN=str(fake), DOCKER="no-such-docker", PODMAN_LOG=str(tmp_path / "log"),
             FAKE_STATE=str(tmp_path / "state"), RESTIC_IMAGE="docker.io/restic/restic@sha256:abc",
             RESTIC_TARGETS="usb", RESTIC_USB=str(tmp_path / "disk" / "restic"), RESTIC_ROLES="workspace queue",
             ROLE_PATH_workspace=str(ws), ROLE_PATH_queue=str(q), RESTIC_ENV=str(tmp_path / "none.env"))
    e.pop("RESTIC_PRUNE", None)
    return e


def run(script, env, *args):
    return subprocess.run(["bash", script, *args], env=env, capture_output=True, text=True)


def log(env):
    return Path(env["PODMAN_LOG"]).read_text()


def test_each_role_saved_read_only_with_its_tag(env):
    r = run(BACKUP, env)
    assert r.returncode == 0, r.stderr
    text = log(env)
    assert f"-v {env['ROLE_PATH_workspace']}:/src/workspace:ro" in text
    assert "backup /src/workspace --host dhoon --tag role=workspace" in text
    assert "backup /src/queue --host dhoon --tag role=queue" in text
    status = (Path(env["HOME"]) / ".config/ai-kit-backup/last-restic.txt").read_text()
    assert status.startswith("usb ok ")


def test_new_repository_is_made_once(env):
    run(BACKUP, env)
    run(BACKUP, env)
    assert log(env).count(" init") == 1


def test_missing_role_is_skipped_not_failed(env):
    env["RESTIC_ROLES"] = "workspace claude"  # ~/.claude doesn't exist in the test home
    r = run(BACKUP, env)
    assert r.returncode == 0 and "claude:" in r.stderr and "skipped" in r.stderr
    assert "role=claude" not in log(env)


def test_offsite_secret_never_on_a_command_line(env):
    env.update(RESTIC_TARGETS="r2", RESTIC_R2="s3:https://acct.r2.cloudflarestorage.com/bucket",
               AWS_ACCESS_KEY_ID="keyid", AWS_SECRET_ACCESS_KEY="supersecret")
    r = run(BACKUP, env)
    assert r.returncode == 0, r.stderr
    text = log(env)
    assert "supersecret" not in text and "keyid" not in text
    assert "-e AWS_SECRET_ACCESS_KEY " in text and "RESTIC_REPOSITORY=s3:https://acct" in text


def test_one_target_failing_is_recorded_and_others_still_run(env):
    env.update(RESTIC_TARGETS="usb r2", RESTIC_R2="s3:https://acct.r2.cloudflarestorage.com/b",
               AWS_ACCESS_KEY_ID="k", AWS_SECRET_ACCESS_KEY="s", FAIL_USB="1")
    r = run(BACKUP, env)
    assert r.returncode == 1
    status = (Path(env["HOME"]) / ".config/ai-kit-backup/last-restic.txt").read_text().splitlines()
    assert status[0].startswith("usb failed ") and status[1].startswith("r2 ok ")


def test_status_says_stale(env):
    path = Path(env["HOME"]) / ".config/ai-kit-backup/last-restic.txt"
    path.write_text(f"usb ok {dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n")
    assert run(BACKUP, env, "--status").returncode == 0
    old = (dt.datetime.now().astimezone() - dt.timedelta(hours=48)).isoformat(timespec="seconds")
    path.write_text(f"usb ok {old}\n")
    r = run(BACKUP, env, "--status")
    assert r.returncode == 1 and "48h ago" in r.stdout


def test_restore_role_into_an_empty_folder_only(env, tmp_path):
    dest = tmp_path / "new-ws"
    r = run(RESTORE, env, "--role", "workspace", "--to", str(dest))
    assert r.returncode == 0, r.stderr
    assert f"-v {dest}:/out" in log(env) and "restore latest:/src/workspace --host dhoon --tag role=workspace" in log(env)
    r = run(RESTORE, env, "--role", "workspace", "--to", str(dest))  # now it has a file: refused
    assert r.returncode == 1 and "isn't empty" in r.stderr


def test_drill_flags_missing_dumps(env, tmp_path):
    env.update(RESTIC_ROLES="workspace rebuild", RESTIC_DRILL_DIR=str(tmp_path / "drill"))
    r = run(RESTORE, env, "--drill")
    assert r.returncode == 1
    assert "workspace" in r.stdout and "ok" in r.stdout and "dump postgres.sql.gz: MISSING" in r.stdout


def test_no_fixed_home_paths():
    # an absolute /home/<user> path; a sub-folder named home (files/home/...) is fine
    absolute = re.compile(r"""(^|[\s"'=:(])/home/""", re.M)
    for name in ("backup_restic.sh", "restore_restic.sh", "restic_lib.sh"):
        assert not absolute.search((ROOT / "scripts" / name).read_text()), name
    assert absolute.search('x="/home/alex/ws"') and not absolute.search("files/home/.gitconfig")


def test_restore_goes_to_restore_dir_by_default(env, tmp_path):
    env["RESTORE_DIR"] = str(tmp_path / "elsewhere")
    r = run(RESTORE, env, "--role", "queue")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "elsewhere" / "queue" / "file.txt").exists()


def test_drill_deletes_its_folder_unless_kept(env, tmp_path):
    env.update(RESTIC_ROLES="workspace", RESTIC_DRILL_DIR=str(tmp_path / "drill"))
    r = run(RESTORE, env, "--drill")
    assert "Drill folder deleted" in r.stdout and not any((tmp_path / "drill").iterdir())
    r = run(RESTORE, env, "--drill", "--keep")
    assert "Drill folder kept" in r.stdout and len(list((tmp_path / "drill").iterdir())) == 1


def test_rebuild_keeps_single_files_by_place(env, tmp_path):
    home = Path(env["HOME"])
    (home / ".gitconfig").write_text("[user]\n")
    extra = home / "notes" / "settings.json"
    extra.parent.mkdir()
    extra.write_text("{}")
    env["REBUILD_EXTRA"] = str(extra)
    r = run(BACKUP, env)
    assert r.returncode == 0, r.stderr
    stage = home / ".local/share/ai-kit-backup/rebuild/files/home"
    assert (stage / ".gitconfig").read_text() == "[user]\n"
    assert (stage / "notes" / "settings.json").exists()


FAKE_CURL = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_LOG"
cat > "$CURL_LOG.stdin"
[ -z "${FAIL_CURL:-}" ]
"""


def test_plain_copy_to_disk_and_r2_without_token_on_command_line(env, tmp_path):
    kdbx = Path(env["HOME"]) / "MyPass.kdbx"
    kdbx.write_bytes(b"\x03\xd9\xa2\x9a keepass")
    curl = tmp_path / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    env.update(PLAIN_FILES=str(kdbx), PLAIN_USB=str(tmp_path / "disk" / "plain"), CURL=str(curl),
               CURL_LOG=str(tmp_path / "curl.log"), PLAIN_R2="https://acct.r2.cloudflarestorage.com/recovery/",
               AWS_ACCESS_KEY_ID="keyid", AWS_SECRET_ACCESS_KEY="supersecret")
    r = run(BACKUP, env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "disk" / "plain" / "MyPass.kdbx").read_bytes() == kdbx.read_bytes()
    argv = (tmp_path / "curl.log").read_text()
    assert "https://acct.r2.cloudflarestorage.com/recovery/MyPass.kdbx" in argv and "--aws-sigv4" in argv
    assert "supersecret" not in argv and "keyid" not in argv
    assert 'user = "keyid:supersecret"' in (tmp_path / "curl.log.stdin").read_text()
    status = (Path(env["HOME"]) / ".config/ai-kit-backup/last-restic.txt").read_text()
    assert "\nplain ok " in "\n" + status


def test_plain_copy_failure_is_reported(env, tmp_path):
    env.update(PLAIN_FILES=str(tmp_path / "missing.kdbx"))
    r = run(BACKUP, env)
    assert r.returncode == 1 and "not found" in r.stderr
