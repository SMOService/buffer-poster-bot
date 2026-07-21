# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.0.1] — 2026-07-21

### Security

- **aiohttp 3.13.4 → 3.14.2** — closes all 11 open Dependabot alerts (HTTP parser bypass, websocket memory-limit bypass, pipelined-request queue, compressed-body bypass, cross-origin redirect with per-request cookies, deserialization of untrusted data, DigestAuth cross-origin credentials, CRLF injection in multipart headers, TLS hostname override, unclosed payload resources, host-only→domain cookies).
- **aiogram 3.28.2 → 3.30.0** — required for the aiohttp bump (3.28.x pins `aiohttp<3.14`); no code changes needed.

## [3.0.0] — 2026-07-20

Major release: **video posts on Binance Square** + reliability hardening that shipped in the upstream production bot since 2.0.0.

**Why 3.0:** the `binance_queue` state machine changed (`status` column: `pending|published|dead` + `next_attempt_at`, schema v4→v5) and the Buffer integration moved to Buffer's new root-query GraphQL API. Migrations are automatic and backward-compatible, but retry behaviour and the Buffer API surface differ from 2.x.

### Added

- 🎬 **Video posts on Binance Square** (`contentType=3`) — forward a video (standalone or inside an album) and it publishes through the official flow: `POST /video/preSign {fileName, size}` → `PUT` bytes → `imageStatus` polling (5s × 36, video processing is slower than images) → `content/add` with `{fileTicket, cover, videoTimeSeconds, isPublish}` (+ `bodyTextOnly` only when the caption is non-empty), verified against `post-video.mjs` from `binance-skills-hub`. The cover is taken from the Telegram video thumbnail. Previously video messages were silently dropped by the handler filter.
  - **20 MB guard** — Telegram Bot API `getFile` refuses files over 20 MB; the bot warns you up front and posts without the video instead of retrying forever.
  - Same no-loss policy as images: if the video upload fails, the post is **not** silently degraded to text-only — it stays queued and retries with the video (legacy behaviour behind `BINANCE_IMAGE_FALLBACK_TEXT=1`).
  - Schema **v5**: `video_file_id`, `video_cover_file_id`, `video_duration` on `binance_queue`; video bytes are fetched from Telegram at publish time (saves Binance upload quota).
- 🔒 **Anti-double-publish** — atomic post claim (`pending|dead → publishing`) closes the race between the scheduler tick and manual `Send now` / `Publish all`; crash recovery resets stuck `publishing` rows to `pending` on startup.
- 🧨 **Dead-letter state machine** — permanent rejects (sensitive words, length limit) and posts that exhaust `BINANCE_MAX_ATTEMPTS` (default 6) go to `dead` instead of retrying every 60s forever. Editing the text revives a dead post.
- 🧭 **Error classification** (`permanent` / `auth` / `quota` / `transient`) drives the retry policy; exponential backoff 5min → ×2 → cap 6h.
- 🚦 **Quota-hold** — on Binance daily-limit errors (`220009`/`220014`) publishing pauses until 00:00 UTC and queued media is preserved.
- 🧹 **Published-post TTL** — published posts are cleaned from `binance_queue` after `BINANCE_PUBLISHED_TTL_DAYS` (default 30, `0` disables), measured from the actual publish time (`published_at`), so the table stops growing forever.
- 🚀 **Ecosystem section** — optional main-menu screen linking hosted bots of the same family (disable with `ECOSYSTEM_LINKS_ENABLED=0`).
- 📣 **`SUNSET_NOTICE`** env — show a migration/announcement banner in `/start` without touching code.
- ✅ **Test suite** — `tests/` (pytest): schema migration v4→v5, video queue round-trip, TTL cleanup, dedup hashes, backoff/dead/revive. Wired into CI.
- New env vars: `BINANCE_MAX_ATTEMPTS`, `BINANCE_BACKOFF_BASE_SEC`, `BINANCE_BACKOFF_MAX_SEC`, `BINANCE_IMAGE_FALLBACK_TEXT`, `BINANCE_PUBLISHED_TTL_DAYS`, `ECOSYSTEM_LINKS_ENABLED`, `SUNSET_NOTICE`.

### Changed

- **Buffer new GraphQL API** — migrated to root `channels(input)` / `posts(input)` queries with organization ids; `assets` is now a required `[AssetInput!]!` list. A `FORBIDDEN` on channels raises a clear, actionable error (subscription / Personal Key permissions) instead of failing silently, and saved channels keep working for posting.
- `/binance` queue UI shows per-post state: `⛔ dead` / `💤 backoff` / `⏳ quota-hold`, attempt counts, last error; `🧹 Purge dead` button; 🎬/🖼/📝 media tags.
- Success notifications include what was attached (photo count or video).
- Logger name normalised to `buffer-poster`.

### Fixed

- Permanent Binance rejects no longer burn the daily upload quota by retrying forever (the original bug showed `attempt_count=24018` on a single post).
- A failed image upload no longer silently publishes the post as bare text — media is never lost.

## [2.0.0] — 2026-05-28

Major release: architecture overhaul + Binance Square media flow.

**Why 2.0:** the single-file `bot.py` (745 LOC) has been split into modules; any fork or downstream patch that targeted `bot.py` line numbers needs to be reworked. New schema migrations are backward-compatible, but the import surface for contributors is different.

### Added

- **Binance Square v2 media flow** — official implementation per `binance/binance-skills-hub`:
  - `POST /image/presignedUrl` → `PUT` raw bytes → `POST /image/imageStatus` polling (3s × 10) → `POST /content/add` with `imageList` (up to 4 photos).
  - `contentType=2` article publishing (title + body + cover) and `contentType=3` video publishing helpers available in `services/binance.py` (SDK layer; UI not yet surfaced).
  - Header `clienttype: binanceSkill` (was `web`).
  - HTTP 504 on `/content/add` treated as success (matches official helper).
  - Known Binance error codes recognised: `220003` (key not found), `220004` (key expired), `220009` (daily post limit), `220014` (daily upload limit), `20002/20022` (sensitive words), `20013` (content length).
- **Inline main menu** — `/start` opens a navigable menu (`📡 Channels / 📋 Queue / 🪙 Binance / 📊 Logs / ⚙️ Settings`) with `← Main menu` on every subscreen.
- **`/logs`** — paginated journal of all publish attempts (Buffer + Binance, success + failed) with filter for failures only. Rotates by `HISTORY_LIMIT` (default 500).
- **`/binance` CRUD** — per-post buttons: `📤 Send now / ✏️ Edit text / 🔁 Reschedule / 🗑 Delete` with confirm dialogs.
- **Pause / resume Binance scheduler** + **⚡ Publish all now** (batch flush with confirmation).
- **Telegram `file_id` storage** — pending Binance posts now keep Telegram `file_id`s; the scheduler downloads fresh bytes from Telegram at publish time. Saves Binance's upload quota and decouples from external image hosts.
- **Database migrations** via `PRAGMA user_version` (schema v3): `post_history`, `kv` tables; `binance_queue` extended with `image_urls`, `image_file_ids`, `content_type`, `title`, `last_error`, `attempt_count`.
- **`BINANCE_USE_IMAGES`** env var (default `1`) to toggle media flow.
- **`HISTORY_LIMIT`** env var (default `500`) for post_history rotation.
- **Import smoke test** in CI.

### Changed

- **Architecture**: `bot.py` (745 LOC) → 18 files. Root: `config.py`, `db.py`, `bot_instance.py`, `keyboards.py`, `scheduler.py`, `state.py`, `bot.py`. Packages: `services/{buffer, binance, uploader}.py`, `handlers/{menu, channels, queue, binance, logs, post, common}.py`.
- **Python 3.11 → 3.12** in Dockerfile and CI (aiogram 3.28 supports 3.10–3.14).
- **aiogram 3.13.1 → 3.28.2** (required for aiohttp 3.13.x).
- Bot commands menu set via `bot.set_my_commands` on startup.

### Security

- **aiohttp 3.13.x stays at 3.13.4** (no change vs v1.3.1).
- Note: v1.3.1 already closed 21 advisories by bumping aiohttp 3.10.10 → 3.13.4; v2.0 keeps that fix and adds the aiogram bump needed for compatibility.

### Migration notes

- **Database**: existing `bot.db` files from v1.x will auto-migrate on first start via incremental `PRAGMA user_version` (no manual steps).
- **Docker**: the `COPY bot.py .` line in `Dockerfile` is now `COPY bot.py bot_instance.py … services/ handlers/`. Custom Dockerfiles need updating.
- **Python**: minimum supported version is now 3.10 (was 3.9 in pyproject; effectively 3.11+ in shipped Dockerfile/CI).
- **Forks**: any code patching `bot.py` directly will need to be re-applied against the new module layout.

## [1.3.1] — 2026-05-26

### Security
- Bumped `aiohttp` from 3.10.10 to 3.13.4 to close 21 advisories
  (1 high, 9 moderate, 11 low — CVE-2024-52303 through CVE-2026-34525).
  All upstream library CVEs; the bot itself was not the attack surface.

## [1.3.0] — 2026-05-26

Initial public release. Code was extracted from a private working copy used by
the maintainer for ~6 months. No functional changes vs. the last private build,
just packaging for self-host.

### Added
- MIT license, EN + RU READMEs.
- Dockerfile + `docker-compose.yml` for non-Railway deploys.
- `.env.example` with annotated variables.
- GitHub CI (ruff + `py_compile` + Docker build sanity).
- Issue / PR templates, SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md.

### Fixed (carried from internal v1.3)
- Facebook publishing required a `postType: post` field — added.
- `service` was not propagated to the publish call — fixed.

## [1.2.0] — internal

### Added
- Carousel support (album of up to 4 photos → single Buffer post).
- Duplicate guard via MD5 of post body.
- `/channels` UI with SQLite-persisted toggles.

## [1.1.0] — internal

### Added
- Binance Square integration with its own scheduler queue.
- Schedule window extended to 240 hours (10 days).

## [1.0.0] — internal

### Added
- Buffer GraphQL publishing for X / Twitter, LinkedIn, Threads.
- Random `dueAt` scheduling in 1–72 h window.
- Single-user lock (`ALLOWED_USER_ID`).

[Unreleased]: https://github.com/SMOService/buffer-poster-bot/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/SMOService/buffer-poster-bot/releases/tag/v2.0.0
[1.3.1]: https://github.com/SMOService/buffer-poster-bot/releases/tag/v1.3.1
[1.3.0]: https://github.com/SMOService/buffer-poster-bot/releases/tag/v1.3.0
