# Changelog

## v1.1.1 (unreleased)

- **Searches no longer unload the main model.** With at most two models in memory, every
  document or knowledge-base search loaded the small embedding model and pushed the main model
  out, so the next answer waited for 17 GB to load again. Now three fit
  (`OLLAMA_MAX_LOADED_MODELS=3`: main, small, and the 0.3 GB embedding model); `update.sh`
  applies it.

## v1.1.0 (2026-10-18)

- **Gemma 4 is the main model** on computers with 28 GB of RAM or more (`gemma4:26b`, about
  17 GB, a mixture of experts with ~4B parameters used per word). Both agents, the planner,
  the knowledge base, the scheduler and plugins use it. On a CPU-only Intel i7 with 64 GB it
  wrote the same news stories, from the same pages, in half the time of Qwen3 30B and invented
  no numbers. Smaller computers keep Qwen3 4B; the small Qwen3 4B stays the fast helper.
  To stay on Qwen3: `MODEL_MAIN=qwen3:30b-a3b-instruct-2507-q4_K_M` in `config/models.env`.
- **At most two models in memory.** Ollama keeps the main and the small model loaded
  (`OLLAMA_MAX_LOADED_MODELS=2`); loading a third big model unloads one instead of the kernel
  stopping Ollama for lack of memory.
- **Quick Assistant on the small fast model.** Everyday questions (weather, calendar,
  reminders) answer in seconds, even while the main model is busy with a long job.
- **Two requests at once** with 28+ GB RAM (`NUM_PARALLEL=2`): a chat no longer waits behind a
  background job (a plugin writing a report, say).
- **Upgrading:** `bash scripts/update.sh` downloads Gemma 4 and says when the agents still use
  the old model; then `bash scripts/06_create_agent.sh` and each plugin's `install.sh`. When
  `ollama ps` shows only Gemma 4 and Qwen3 4B, `ollama rm qwen3:30b-a3b-instruct-2507-q4_K_M`
  frees 19 GB.
- **Knowledge base of world news and markets.** The models know the world only up to their
  training (late 2024). Every night Dhoon now adds short weekly notes of world news events and
  market events since then (`~/agent-workspace/knowledge/`): world news from Wikipedia's
  "Current events" pages (no sports, arts or science items), a table of how the world's main
  markets moved computed from prices, and for the last week recent headlines from your SearXNG,
  summarised by your main model, with monthly notes and an overview of what changed. The new
  `search_knowledge` tool finds them in about a second, so questions about recent world events
  or markets are answered faster and more reliably than with a web search each time. On by
  default (`KNOWLEDGE=off` to switch off); the catch-up takes a few nights, or
  `bash scripts/knowledge.sh --catch-up`.
- **Notes are finished or redone.** Every note ends with a completion marker written in the
  same atomic save, so a run that errors, is stopped or loses power never leaves half a note;
  unfinished or partial weeks (Wikipedia still missing days) are written again.
- **Confidence from independent sources.** Every item of a weekly note gets a confidence from
  1 to 10, counted by code from how many independent sources report it: the outlets Wikipedia
  cites for it plus independent websites found by a search through your SearXNG, shown next
  to the item. `knowledge.sh --rate-all` rates notes written before.

## v1.0.1 (2026-10)

- **Your chats are backed up.** Before each encrypted backup, the chat website's database
  (chats, accounts, agents, memories) is exported to `runtime/chat-backup/` and backed up
  with the rest. `bash scripts/restore.sh --chats` puts it back. Files uploaded in chats are
  optional (`BACKUP_CHAT_UPLOADS=yes`).
- **Memory (optional, off by default).** The installer asks whether to switch it on. Then
  say *"Remember that…"* and the assistant remembers it in later chats (it saves nothing
  unasked; see and delete memories in Settings -> Personalization), using the small fast
  model and your own database. Change it with `MEMORY=on` or `off` in `config/models.env`,
  then `bash scripts/05_librechat.sh`. When on, each person can switch it off in Settings ->
  Personalization.
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
