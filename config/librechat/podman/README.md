# Chat stack on rootless Podman (Quadlets)

Replaces the Docker Compose stack. One pod, `librechat`, with the same container names as before
(`LibreChat`, `admin-panel`, `chat-mongodb`, `chat-meilisearch`, `vectordb`, `rag_api`), so
`scripts/backup.sh`, `backup_restic.sh` and `restore.sh` work with `DOCKER=podman`.

- The containers share localhost inside the pod. The pod publishes 3080 (chat) and 3000 (admin panel).
- `host.docker.internal` = this computer's own 127.0.0.1 (pasta `--map-host-loopback`), so the toolbox
  (`ai-toolbox.service`, a user unit) listens on 127.0.0.1:8001 only.
- Files the units expect in `~/dhoon/runtime/LibreChat/`: `.env`, `librechat.yaml`, `admin-panel.env`
  (`SESSION_SECRET=` from `ADMIN_PANEL_SESSION_SECRET` in `.env`), `branding/`, `images/`, `uploads/`,
  `logs/`, `skill/`, `data-node/`, `meili_data_v1.35.1/`.
- Needs the `catatonit` package (the pod's helper container).
- Images are pinned by digest where LibreChat publishes them; mongo, meilisearch and pgvector by version.

Install: see docs/RESTORE.md, step 12.
