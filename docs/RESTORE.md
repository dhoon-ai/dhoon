# RESTORE: a working computer again, from the backups only

Last proven: Debian testing (forky), 8 Nov 2026, from the restic backup on R2. Steps marked
**(not proven yet)** have not been run end to end on a restored machine; fix this file as soon as they are.
**Rule: whenever a setup step changes (a new service, a new folder, a new secret), change this file in
the same commit.**

Nothing here assumes the old computer, the old disk layout, the old user name or the old paths. Every
place is a variable in `paths.env` (step 1). Commands are chained with `&&`, so a failure stops the step.

## 0. What you need (kept outside the computer)
- The password-manager file (`MyPass.kdbx`) from the USB stick or its off-site copy, and its master password.
- In it: the restic password, `restic.env` (off-site endpoint + token, USB path), the age recovery key
  (`recovery-key.txt`; or the YubiKey), the backup-repo SSH key, a GitHub login.
- In it: `restore.env` (this computer's private values: repo list, host name, address; template below).
- The USB backup disk (optional: everything is also off-site).

## 1. Choose the places: `paths.env`
Write `~/paths.env` (any disk layout works; these are examples) and load it in every shell you use for
this restore: `set -a && . ~/paths.env && set +a`.
```bash
PROJECT_DIR=/srv/project            # repos: dhoon, plugins, blocks, sites, dev worktrees
AGENT_DIR=/srv/agent                # workspace, Beam queue, container storage
MODELS_DIR=/srv/models/ollama       # Ollama models (~21 GB)
BACKUP_USB=/media/backup            # USB backup disk mount point (empty = no USB)
RESTORE_DIR=$HOME/restore           # scratch for restored roles (holds secrets: delete at the end)
BACKUP_CONF=$HOME/.config/ai-kit-backup
WORKSPACE=$AGENT_DIR/agent-workspace
BEAM_QUEUE=$AGENT_DIR/beam-queue
CONTAINER_STORAGE=$AGENT_DIR/containers/storage
DHOON_HOME=$PROJECT_DIR/dhoon
VENV=$DHOON_HOME/runtime/venv
```
Until the code reads these itself (Idea 19, Rule 2), it looks in `~/<name>`; step 7 links each name to
its real place, so the places above can be anything.

`restore.env` template (private; real copy in the password manager and in the backupconf role):
```bash
NEW_HOSTNAME=office-pc
STATIC_IP=192.168.1.x/24 GATEWAY=192.168.1.y DNS=192.168.1.z IFACE=eno1   # empty = DHCP
# REPOS: "<folder under PROJECT_DIR>|<git url>" one per line
REPOS="dhoon|https://github.com/<public-org>/dhoon.git
dhoon/plugins/<name>|https://github.com/<org>/dhoon-<name>.git
blocks|https://github.com/<org>/blocks.git
..."
DEV_WORKTREES="dhoon dhoon/plugins/cloud dhoon/plugins/mentor dhoon/plugins/newsroom dhoon/plugins/studio blocks"
```

## 2. Base system (the only sudo step)
Install Debian (or Ubuntu) normally with your user. Then:
```bash
sudo apt-get update && sudo apt-get install -y git curl gnupg rsync age keepassxc build-essential python3-dev python3-venv podman uidmap passt libpam-systemd dbus-user-session && sudo loginctl enable-linger "$USER" && echo "127.0.1.1 $NEW_HOSTNAME" | sudo tee -a /etc/hosts && sudo hostnamectl set-hostname "$NEW_HOSTNAME" && sudo timedatectl set-timezone America/Toronto
```
- Disks: create and mount them where `paths.env` says (fstab with `nofail`), then
  `sudo mkdir -p "$PROJECT_DIR" "$AGENT_DIR" "$MODELS_DIR" && sudo chown "$USER": "$PROJECT_DIR" "$AGENT_DIR"`.
- USB backup disk (NTFS, so it needs an owner): by its stable id, never `/dev/sdX`:
  `ls /dev/disk/by-id/ | grep usb`, then
  `echo "/dev/disk/by-id/<usb-id>-part1 $BACKUP_USB auto nosuid,nodev,nofail,uid=$(id -u),gid=$(id -g),umask=077 0 0" | sudo tee -a /etc/fstab && sudo mkdir -p "$BACKUP_USB" && sudo systemctl daemon-reload && sudo mount "$BACKUP_USB" && touch "$BACKUP_USB/.write-test"`.
- GitHub CLI (not in Debian testing): add the cli.github.com apt repo (keyring
  `/etc/apt/keyrings/githubcli-archive-keyring.gpg`), `sudo apt-get install -y gh`, then
  `gh auth login -h github.com -p https -w && gh auth setup-git` (the token is not in the backup).
- Static address (optional): `nmcli con mod <connection> ipv4.method manual ipv4.addresses $STATIC_IP ipv4.gateway $GATEWAY ipv4.dns $DNS`.

## 3. Secrets from the password manager
Open `MyPass.kdbx` in KeePassXC. Save `restic.env` and `restic-password` into `$BACKUP_CONF`
(`mkdir -p -m 700 "$BACKUP_CONF"`), and `restore.env` next to `paths.env`. In `restic.env`, set
`RESTIC_USB=$BACKUP_USB/restic`, `PLAIN_USB=$BACKUP_USB/plain`, `USB_MOUNT=$BACKUP_USB` (backups refuse to
write when the disk is not mounted there) and `RESTORE_DIR` to the value from `paths.env`.

## 4. Get Dhoon and the backup (restic, rootless Podman, image pinned by digest)
```bash
git clone https://github.com/<public-org>/dhoon.git "$DHOON_HOME" && cd "$DHOON_HOME" && bash scripts/restore_restic.sh --list --from r2 && bash scripts/restore_restic.sh --drill --from r2 && for r in workspace queue claude config backupconf ssh rebuild; do bash scripts/restore_restic.sh --role $r --from r2 || echo "FAILED $r"; done && du -sh "$RESTORE_DIR"/*
```
Use `--from usb` with the USB disk mounted. Each role lands in `$RESTORE_DIR/<role>`.

## 5. Put the roles in place
```bash
mv "$RESTORE_DIR/workspace" "$WORKSPACE" && mv "$RESTORE_DIR/queue" "$BEAM_QUEUE" && mv "$RESTORE_DIR/claude" ~/.claude && mv "$RESTORE_DIR/ssh" ~/.ssh && chmod 700 ~/.ssh && chmod 600 ~/.ssh/* && { chmod 644 ~/.ssh/*.pub 2>/dev/null || true; } && rsync -a --ignore-existing "$RESTORE_DIR/backupconf/" "$BACKUP_CONF/" && rsync -a --exclude dhoon "$RESTORE_DIR/rebuild/files/home/" ~/ && rsync -a --ignore-existing "$RESTORE_DIR/rebuild/files/home/dhoon/" "$DHOON_HOME/" && mkdir -p "$DHOON_HOME/config" && rsync -a --ignore-existing "$RESTORE_DIR/config/" "$DHOON_HOME/config/" && echo ROLES-OK
```
Check what still names the old home: `grep -rlI -e '/home/' ~/.config ~/.claude-beam ~/.local/bin ~/.bashrc ~/.claude.json "$DHOON_HOME/config"`.

## 6. Repos and dev worktrees
```bash
cd "$PROJECT_DIR" && printf '%s\n' "$REPOS" | while IFS='|' read -r dir url; do [ -n "$dir" ] || continue; if [ -e "$dir/.git" ]; then echo "have $dir"; elif [ -d "$dir" ]; then git clone -q --no-checkout "$url" "$dir.tmp" && mv "$dir.tmp/.git" "$dir/" && rmdir "$dir.tmp" && git -C "$dir" reset -q --hard && echo "ok $dir"; else git clone -q "$url" "$dir" && echo "ok $dir"; fi || echo "FAILED $dir"; done
for r in $DEV_WORKTREES; do case $r in dhoon/plugins/*) dev="dhoon-dev/${r#dhoon/}";; *) dev="$r-dev";; esac; git -C "$r" worktree add -q -b dev "$PROJECT_DIR/$dev" main && echo "dev $dev"; done
```
(Plugins sit inside `dhoon/plugins/`, which the dhoon clone already made: hence the no-checkout move.)
Repos not on GitHub yet are restored from their bundle in the backup: `git clone -q <name>.bundle "$PROJECT_DIR/<name>"`.

## 7. Link the names the code expects
```bash
for d in $(printf '%s\n' "$REPOS" | cut -d'|' -f1 | grep -v /) dhoon-dev blocks-dev; do [ -e "$PROJECT_DIR/$d" ] && ln -sfn "$PROJECT_DIR/$d" ~/"$d"; done && ln -sfn "$WORKSPACE" ~/agent-workspace && ln -sfn "$BEAM_QUEUE" ~/beam-queue && ls -l ~ | grep -- '->'
```

## 8. Python venv and the test suites
```bash
python3 -m venv "$VENV" && "$VENV/bin/pip" install -q --upgrade pip && grep -v -e '^-e ' -e ' @ file://' "$RESTORE_DIR/rebuild/pip-freeze.txt" > "$RESTORE_DIR/pip-pypi.txt" && "$VENV/bin/pip" install -q -r "$RESTORE_DIR/pip-pypi.txt" && "$VENV/bin/pip" install -q "$PROJECT_DIR/receipt-reader" && "$VENV/bin/pip" check && echo VENV-OK
V="$VENV/bin/python"; for r in ~/dhoon ~/dhoon/plugins/cloud ~/dhoon/plugins/mentor ~/dhoon/plugins/studio; do echo "== $r"; (cd $r && $V -m pytest -q 2>&1 | tail -1); done; (cd ~/dhoon/plugins/newsroom && PYTHONPATH=$HOME/blocks/src $V -m pytest -q 2>&1 | tail -1); (cd ~/blocks && PYTHONPATH=src $V -m pytest -q 2>&1 | tail -1)
```
8 Nov: dhoon 255, cloud 166, mentor 98, studio 22, newsroom 697, blocks 129, all passed.

## 9. Ollama (native) and the models
```bash
curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION=0.40.11 sh && sudo systemctl stop ollama && sudo mkdir -p /etc/systemd/system/ollama.service.d && sudo cp "$RESTORE_DIR"/rebuild/files/system/etc/systemd/system/ollama.service.d/*.conf /etc/systemd/system/ollama.service.d/ && printf '[Service]\nEnvironment="OLLAMA_MODELS=%s"\n' "$MODELS_DIR" | sudo tee /etc/systemd/system/ollama.service.d/zz-models.conf && sudo chown -R ollama:ollama "$MODELS_DIR" && sudo systemctl daemon-reload && sudo systemctl restart ollama && sleep 3 && ollama -v && ollama list
```
`zz-models.conf` loads last, so it overrides the model folder in the restored settings (32k context,
keep-alive -1, 2 parallel, 3 loaded, CPU cap, listening on all addresses).
Models are NOT in the backup: copy the folder from a disk that has them, or
`awk 'NR>1{print $1}' "$RESTORE_DIR/rebuild/models.txt" | xargs -n1 ollama pull` (~21 GB).
Check: `ollama run qwen3:4b-instruct-2507-q4_K_M "say ok"` (use the FULL tag).

## 10. Podman storage
```bash
mkdir -p ~/.config/containers "$CONTAINER_STORAGE" && printf '[storage]\ndriver = "overlay"\ngraphroot = "%s"\n' "$CONTAINER_STORAGE" > ~/.config/containers/storage.conf && podman info --format '{{.Store.GraphRoot}}' && podman run --rm docker.io/library/alpine echo ok
```
Only inside systemd-nspawn (a chroot drill) also add `containers.conf` with `default_sysctls = []` and
`pidns = "host"`; never on a real boot.

## 11. Claude: desktop app, Claude Code, Beam
- Desktop app: apt repo `https://downloads.claude.ai/claude-desktop/apt/stable` (key
  `https://downloads.claude.ai/claude-desktop/key.asc`, fingerprint 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE),
  `sudo apt-get install -y claude-desktop && sudo usermod -aG kvm "$USER"`; sign in; in Claude, add this
  computer and switch "Require this computer" back on for the scheduled tasks that need it.
- Claude Code: `curl -fsSL https://claude.ai/install.sh | bash`, then `claude` once to sign in.
- Beam: `ln -sf ~/dhoon/plugins/cloud/beam ~/.local/bin/beam && ln -sf ~/dhoon/plugins/cloud/ask-claude ~/.local/bin/ask-claude`.
  Test: `beam --local --report -p "Reply with one word: ok"` **(not proven yet)**. The cloud model needs
  `ollama signin` (paid plan); without it the Beam queue falls back to the local model.

## 12. Services and timers
Only ONE computer may run the timers (otherwise you get double drafts, e-mails and pushes): stop
them on the old one first, if it still runs.

**SearXNG** (proven 8 Nov; rootless Podman Quadlet, starts at boot through linger):
```bash
d="$AGENT_DIR/containers/searxng" && mkdir -p "$d" ~/.config/containers/systemd && cp "$DHOON_HOME/runtime/searxng/settings.yml" "$d/" && printf '[Unit]\nDescription=SearXNG\nAfter=network-online.target\n\n[Container]\nImage=docker.io/searxng/searxng:latest\nContainerName=searxng\nPublishPort=127.0.0.1:8888:8080\nVolume=%s:/etc/searxng:U\n\n[Service]\nRestart=always\nTimeoutStartSec=900\n\n[Install]\nWantedBy=default.target\n' "$d" > ~/.config/containers/systemd/searxng.container && systemctl --user daemon-reload && systemctl --user start searxng && sleep 10 && curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8888/search?q=test&format=json'
```
200 = OK. (`settings.yml` comes from `runtime/searxng`, which is not in the backup yet: see gaps.)

**Units and timers** (proven 8 Nov), from the `rebuild` role:
```bash
R="$RESTORE_DIR/rebuild" && mkdir -p ~/.config/systemd/user && for f in "$R"/units/systemd-user/*.service "$R"/units/systemd-user/*.timer "$R"/units/systemd-user/*.path; do sed "s#/home/[^/]*/#$HOME/#g" "$f" > ~/.config/systemd/user/"$(basename "$f")"; done && cp -r "$R/units/systemd-user/beam-queue.service.d" ~/.config/systemd/user/ && systemctl --user daemon-reload && (cd ~/.config/systemd/user && systemctl --user enable --now *.timer beam-queue.path)
S="$R/files/system/etc/systemd/system" && T=$(mktemp -d) && for f in "$S"/ai-*; do n=$(basename "$f"); [ "$n" = ai-toolbox.service ] && continue; if [ -d "$f" ]; then cp -r "$f" "$T/"; else sed -e "s#/home/[^/]*/#$HOME/#g" -e "s#^User=.*#User=$USER#" "$f" > "$T/$n"; fi; done && sudo cp -r "$T"/* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now ai-backup.timer ai-health-check.timer ai-knowledge.timer ai-mentor.timer ai-mentor-india.timer ai-mentor-morning.timer ai-scheduler.timer ai-studio-guide.timer ai-studio-leads.timer ai-studio-site.timer ai-warmup.timer ai-files.service ai-mentor-dashboard.service && sudo systemctl enable ai-model-preload.service
systemctl --user list-timers --no-pager && systemctl list-timers 'ai-*' --no-pager && systemctl --failed --no-pager && systemctl --user --failed --no-pager
```
`ai-toolbox` is left out until the chat stack runs (it wants Docker's bridge, `172.17.0.1`).

**Not proven yet:**
- Containers: LibreChat stack (mongo, meilisearch, pgvector, rag_api,
  admin panel), agent-sandbox (local build), site-preview (hugo); images and digests in
  `rebuild/images-*.txt`; settings in `$DHOON_HOME/config/{searxng,librechat}`; chats from
  `rebuild/mongo.archive.gz` + `postgres.sql.gz` (`scripts/restore.sh --chats`).
- ai-toolbox on Podman: change `BIND_HOST` and drop `Requires=docker.service`.
- Crontab: `rebuild/crontab.txt` (empty on 8 Nov).
- First backup run from the new computer: `bash ~/dhoon/scripts/backup_restic.sh && bash ~/dhoon/scripts/backup_restic.sh --status`.
- Desktop: KeePassXC, VS Code, PairDesk (.deb), GNOME Remote Login.

## 13. Boot (only when this disk shares a computer with other systems)
Give the system its own firmware entry through shim (Secure Boot), first in the order; leave other
systems' disks alone. `efibootmgr -v` shows the entries.

## 14. Finish
- `rm -rf "$RESTORE_DIR"` (it holds secrets in plain text) once everything runs.
- Run a backup from the new computer and a `--drill` of it.

## Known gaps in the backup (fix in backup_restic.sh, then remove from this list)
- Repos not on GitHub (PairDesk, its site): only as bundles handed over by hand.
- `model-check` was missing from the restore's repo list.
- Personal folders: Documents, Pictures, Videos, Desktop, the PairDesk shared folders, `~/.config/pairdesk`.
- Runtime state not in `rebuild`: `runtime/warm/`, `last-chat`, `mentor-dashboard.pin`, `benchmark.txt`
  (all rebuildable), `runtime/searxng/settings.yml` (check it matches `config/searxng`).
- `runtime/searxng/settings.yml` is the live SearXNG setting; `config/searxng/settings.yml` (backed up) differs.
- SearXNG image is `:latest`, not pinned by digest.
- The LibreChat source checkout (`runtime/LibreChat`) is re-cloned, not restored: pin its commit.
