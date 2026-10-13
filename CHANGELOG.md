# Changelog

## v1.0.1 (2026-10)

- **Your chats are backed up.** Before each encrypted backup, the chat website's database
  (chats, accounts, agents, memories) is exported to `runtime/chat-backup/` and backed up
  with the rest. `bash scripts/restore.sh --chats` puts it back. Files uploaded in chats are
  optional (`BACKUP_CHAT_UPLOADS=yes`).
- **Memory.** Say *"Remember that…"* and the assistant remembers it in later chats (it saves
  nothing unasked; see and delete memories in Settings -> Personalization), using the
  small fast model and your own database. Each person can switch it off in Settings ->
  Personalization; `MEMORY=off` in `config/models.env` switches it off for everyone.
- **Updates download Ollama only when needed.** `update.sh` used to download Ollama
  (~1.5 GB) and restart it every time; now only when the version changes.
  `OLLAMA_VERSION` in `config/models.env` can pin one.
- **Excel and Word.** The Assistant can make `.xlsx` and `.docx` files (the code sandbox now
  has openpyxl and python-docx) and `read_document` / `search_document` read Excel files.
- **Click to open created files.** When the Assistant makes a file, its name in the chat is a
  link that opens or downloads it, also from your phone at home. Links are signed, work only
  for that file in the workspace and expire after 7 days (new service `ai-files`, port 3081;
  `update.sh` sets it up). The new `file_link` tool makes a fresh link.

## v1.0.0 (2026-10)

The first release under the name **Dhoon** (from Hindi धुन, *dhun*: the inner spark, the drive
that keeps going until the work is done). Website: https://dhoon.knowreva.com

**Your private AI assistant, on your own computer**
- One-command install on Ubuntu/Debian:
  `curl -fsSL https://dhoon.knowreva.com/install.sh | bash`. It resumes after a failure and
  explains how to fix a step by hand.
- Local models with Ollama (Qwen3 Instruct, sized to your RAM), no graphics card needed.
- A ChatGPT-style chat website (LibreChat), branded Dhoon, usable from any device at home.
- Two agents: the full **Assistant** and the fast **Quick Assistant** for everyday questions.

**What it can do**
- Private web search (SearXNG), reading pages (article text only), research with sources.
- Questions about your PDFs and documents, searched locally (keywords + meaning).
- Files and Python in an isolated sandbox: numbers, charts, reports.
- Stocks: prices, history and analysis for research and learning (not financial advice).
- Weather, calendars (local, CalDAV, Google), reminders and scheduled tasks.
- E-mail results, only to addresses you allow.
- Your own tools as private plugins, kept out of the public repo.

**Built for modest hardware**
- Main model kept in memory, instructions read in advance (fast first answer), short tool
  outputs, CPU-vs-GPU speed test.

**Settings in one place**
- The guide's "After installing: your settings" lists every optional setting (e-mail,
  calendars, models, backup, sign-up lock, your own tools); `scripts/check.sh` shows which
  are set up, without showing any secret.

**Reliability and safety**
- Status, check, logs and restart scripts; automatic start after a reboot.
- Encrypted daily backups to a git repo you own (recovery key and optional YubiKey).
- LibreChat (code and images) and SearXNG pinned to the tested versions.
- 143 automated tests and CI on every push.
