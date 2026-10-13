"""scripts/backup.sh + scripts/restore.sh end to end, with real age keys and a local bare git
repo standing in for GitHub. A second X25519 key stands in for the security key."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = str(ROOT / "scripts" / "backup.sh")
RESTORE = str(ROOT / "scripts" / "restore.sh")
pytestmark = pytest.mark.skipif(not shutil.which("age") or not shutil.which("age-keygen"), reason="age not installed")


def keygen(path):
    subprocess.run(["age-keygen", "-o", str(path)], check=True, capture_output=True)
    return [line for line in path.read_text().splitlines() if "public key" in line][0].split()[-1]


def run(args, env, ok=True):
    r = subprocess.run(["bash", *args], env=env, capture_output=True, text=True)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def kit(tmp_path):
    home = tmp_path / "home"
    conf = home / ".config/ai-kit-backup"
    conf.mkdir(parents=True)
    y = keygen(conf / "yubikey.txt")
    r = keygen(conf / "recovery-key.txt")
    (conf / "recipients.txt").write_text(f"{y}\n{r}\n")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    (conf / "backup.env").write_text(
        f"BACKUP_REPO={remote}\nBACKUP_KEEP=2\nBACKUP_PATHS='ai-kit/plugins agent-workspace/notes missing-folder'\n"
    )
    plug = home / "ai-kit/plugins/secret"
    plug.mkdir(parents=True)
    (plug / "tools.py").write_text("PRIVATE = 1\n")
    (plug / "__pycache__").mkdir()
    (plug / "__pycache__" / "x.pyc").write_text("junk")
    subprocess.run(["git", "-C", str(plug), "init", "-q"], check=True)
    notes = home / "agent-workspace/notes"
    notes.mkdir(parents=True)
    (notes / "a.txt").write_text("cash 123")
    env = dict(
        os.environ,
        HOME=str(home),
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    for k in ("BACKUP_CONF", "BACKUP_REPO_DIR", "WORKSPACE_DIR", "BACKUP_MAX_MB"):
        env.pop(k, None)
    return dict(home=home, conf=conf, remote=remote, env=env, tmp=tmp_path)


def remote_files(kit):
    return git("--git-dir", str(kit["remote"]), "ls-tree", "-r", "--name-only", "main").split()


def test_not_configured_is_silent(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    env.pop("BACKUP_CONF", None)
    assert run([BACKUP, "--if-configured"], env).stdout == ""
    assert run([BACKUP], env, ok=False).returncode == 1


def test_backup_is_encrypted_one_commit_and_pruned(kit):
    env = kit["env"]
    for _ in range(3):
        r = run([BACKUP], env)
        time.sleep(1.1)  # backup names carry the time to the second
    assert "Skipping ~/missing-folder" in r.stdout
    files = remote_files(kit)
    backups = [f for f in files if f.startswith("backups/")]
    assert len(backups) == 2
    assert {"README.md", "recipients.txt", "yubikey-identity.txt"} <= set(files)
    assert git("--git-dir", str(kit["remote"]), "rev-list", "--count", "main").strip() == "1"
    blob = subprocess.run(
        ["git", "--git-dir", str(kit["remote"]), "show", f"main:{backups[0]}"], capture_output=True, check=True
    ).stdout
    assert blob.startswith(b"age-encryption.org") and b"cash 123" not in blob and b"PRIVATE" not in blob


def test_too_big_is_refused(kit):
    r = run([BACKUP], dict(kit["env"], BACKUP_MAX_MB="0"), ok=False)
    assert r.returncode == 1 and "too big" not in r.stdout and "refuse" in r.stderr
    assert not (kit["remote"] / "refs/heads/main").exists()


def test_opens_with_either_key_and_not_a_stranger(kit):
    env = kit["env"]
    run([BACKUP], env)
    for key in ("recovery-key.txt", "yubikey.txt"):
        assert "opens correctly" in run([RESTORE, "--test", "--key", str(kit["conf"] / key)], env).stdout
    keygen(kit["tmp"] / "other.txt")
    r = run([RESTORE, "--test", "--key", str(kit["tmp"] / "other.txt")], env, ok=False)
    assert r.returncode == 1 and "Could not open" in r.stderr
    assert not list(kit["home"].glob("restore-*"))


def test_extract_list_and_apply_everything(kit):
    env = kit["env"]
    run([BACKUP], env)
    r = run([RESTORE, "--list", "--key", str(kit["conf"] / "recovery-key.txt")], env)
    assert "~/ai-kit/plugins" in r.stdout and "backup-" in r.stdout
    r = run([RESTORE, "--key", str(kit["conf"] / "recovery-key.txt")], env)
    out = next(kit["home"].glob("restore-*"))
    assert (out / "backup/home/ai-kit/plugins/secret/tools.py").read_text() == "PRIVATE = 1\n"
    assert not (out / "backup/home/ai-kit/plugins/secret/__pycache__").exists()
    shutil.rmtree(out)
    (kit["home"] / "agent-workspace/notes/a.txt").write_text("changed")
    run([RESTORE, "--apply", "--key", str(kit["conf"] / "recovery-key.txt")], env)
    assert (kit["home"] / "agent-workspace/notes/a.txt").read_text() == "cash 123"
    aside = list((kit["home"] / "agent-workspace").glob("notes.before-restore-*"))
    assert aside and (aside[0] / "a.txt").read_text() == "changed"
    assert not list(kit["home"].glob("restore-*"))  # unencrypted copy cleaned up


def test_removed_plugin_comes_back_with_history_on_a_new_computer(kit):
    env = kit["env"]
    plug = kit["home"] / "ai-kit/plugins/secret"
    subprocess.run(["git", "-C", str(plug), "add", "tools.py"], check=True, env=env)
    subprocess.run(["git", "-C", str(plug), "commit", "-qm", "first"], check=True, env=env)
    run([BACKUP], env)
    shutil.rmtree(plug)
    # new computer: no settings, no local clone; key from the USB stick, backups via --from
    usb = kit["tmp"] / "usb-recovery.txt"
    shutil.move(kit["conf"] / "recovery-key.txt", usb)
    shutil.rmtree(kit["conf"])
    shutil.rmtree(kit["home"] / ".local/share/ai-kit-backup")
    r = run([RESTORE, "--from", str(kit["remote"]), "--key", str(usb), "--only", "ai-kit/plugins/secret"], env)
    assert "Restored ~/ai-kit/plugins/secret" in r.stdout
    assert (plug / "tools.py").read_text() == "PRIVATE = 1\n"
    assert "first" in git("-C", str(plug), "log", "--oneline")
    assert not (kit["home"] / "agent-workspace/notes.before-restore-x").exists()


def test_only_rejects_paths_outside_home(kit):
    r = run([RESTORE, "--only", "../etc", "--key", "x"], kit["env"], ok=False)
    assert r.returncode == 2


def test_chats_are_backed_up_and_put_back(kit):
    """The chat database is exported before each backup (outside the workspace), travels
    encrypted with the rest, and `restore.sh --chats` loads it back. A stand-in docker."""
    home, env = kit["home"], kit["env"]
    dhoon = home / "dhoon"
    (dhoon / "scripts").mkdir(parents=True)
    for s in ("backup.sh", "restore.sh"):
        shutil.copy(ROOT / "scripts" / s, dhoon / "scripts" / s)
    log = kit["tmp"] / "docker.log"
    fake = kit["tmp"] / "docker"
    fake.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> {log}\n'
        'case "$1" in\n'
        "  ps) echo chat-mongodb; echo LibreChat ;;\n"
        '  exec) case "$*" in *mongodump*) printf "CHATS-ARCHIVE-1" ;; *mongorestore*) cat > '
        + f"{kit['tmp']}/restored"
        + " ;; esac ;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    env = dict(env, DOCKER=str(fake))
    r = run([str(dhoon / "scripts/backup.sh")], env)
    assert "Chats exported" in r.stdout
    dump = dhoon / "runtime/chat-backup/librechat.archive.gz"
    assert dump.read_bytes() == b"CHATS-ARCHIVE-1" and oct(dump.stat().st_mode)[-3:] == "600"
    assert not (home / "agent-workspace/.chat-backup").exists()  # never where the agent's tools can read it

    key = str(kit["conf"] / "recovery-key.txt")
    assert "dhoon/runtime/chat-backup" in run([str(dhoon / "scripts/restore.sh"), "--list", "--key", key], env).stdout
    r = run([str(dhoon / "scripts/restore.sh"), "--chats", "--yes", "--key", key], env)
    assert "Chats, accounts and agents are back" in r.stdout
    assert (kit["tmp"] / "restored").read_bytes() == b"CHATS-ARCHIVE-1"
    assert "--drop" in log.read_text() and "restart LibreChat" in log.read_text()
    assert not list(home.glob("restore-*"))  # unencrypted copy cleaned up

    r = run([str(dhoon / "scripts/backup.sh")], dict(env, BACKUP_CHATS="no"))
    assert "Chats exported" not in r.stdout
