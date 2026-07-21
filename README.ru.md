# Buffer Poster Bot

[![CI](https://github.com/SMOService/buffer-poster-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/SMOService/buffer-poster-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![aiogram 3.30](https://img.shields.io/badge/aiogram-3.30-blueviolet)](https://docs.aiogram.dev/)

> Self-hosted Telegram-бот: пересылаешь пост — он планирует публикацию со случайным временем (1–240 ч) на всех каналах **Buffer** (X, LinkedIn, Threads, Bluesky, Mastodon, Facebook, Instagram, …) и в **Binance Square**. Закинул 50 постов = месяц контента вперёд. Фото, альбомы, **видео**, inline-меню, полный CRUD над очередью, журнал публикаций.

[English version](README.md)

---

## Что нового в v3.0

- 🎬 **Видео-посты в Binance Square** — пересланное видео (одиночное или в альбоме) публикуется как нативный видео-пост Square (`contentType=3`) через официальный flow: `POST /video/preSign {fileName, size} → PUT → imageStatus polling → content/add {fileTicket, cover, videoTimeSeconds}` (сверено с `post-video.mjs` из binance-skills-hub). Cover берётся из Telegram-превью. Видео больше лимита Bot API 20 MB (`getFile`) детектится сразу с внятным предупреждением — вместо обречённого retry-цикла.
- 🧨 **Dead-letter state machine** — permanent-реджекты Binance (sensitive words, лимит длины) и исчерпавшие попытки посты перестают ретраиться и уходят в `⛔ dead` (правка текста воскрешает). Закрывает баг, когда один отклонённый пост ретраился каждые 60с вечно и выжигал дневную upload-квоту.
- ⏳ **Exponential backoff + quota-hold** — transient-ошибки: 5 мин → ×2 → cap 6 ч; дневной лимит → пауза до 00:00 UTC, все медиа сохраняются.
- 🖼🎬 **Медиа не теряются** — при сбое upload пост НЕ деградирует молча до text-only, а остаётся в очереди и ретраится С медиа (старое поведение — `BINANCE_IMAGE_FALLBACK_TEXT=1`).
- 🔄 **Новый GraphQL API Buffer** — миграция на root-запросы `channels(input)` / `posts(input)`; проблемы авторизации теперь дают actionable-ошибку вместо тихого фейла.
- 🧹 **TTL опубликованных** — таблица очереди чистится через `BINANCE_PUBLISHED_TTL_DAYS` (default 30).
- ✅ **Тесты в CI** — pytest: миграции схемы, видео-очередь, TTL, dedup, retry-переходы.

Полная история — в [`CHANGELOG.md`](CHANGELOG.md).

---

## Что делает

Пересылаешь пост в бота — бот выбирает случайный `dueAt` в окне `1–240 ч` и планирует публикацию на всех включённых каналах Buffer + Binance Square. Один drop из 50 постов = ~месяц контента вперёд.

```mermaid
flowchart LR
    A[Форвард поста<br/>в Telegram] --> B{Альбом?}
    B -- да, 1.5с буфер --> C[Загрузка фото<br/>imgbb → catbox → 0x0]
    B -- нет --> C
    C --> D[Случайный dueAt<br/>1–240 ч от now]
    D --> E[Buffer GraphQL<br/>createPost]
    D --> F[Очередь Binance<br/>SQLite + Telegram file_ids]
    E --> G[X / LinkedIn / Threads<br/>Bluesky / Mastodon / FB / IG]
    F --> H[Фоновый scheduler<br/>тик каждые 60с]
    H --> I[Binance Square v2<br/>presignedUrl → PUT → imageStatus → content/add<br/>фото: imageList · видео: fileTicket]
    I --> J[ЛС: ссылка на пост]
```

### Пример сессии

```
Ты ▸ [форвард поста: «GM ☀️ сегодня катим новую фичу»]

Бот ▸ Buffer ⏰ 23 May 2026 14:30 UTC
        ✅ 🐦 @yourhandle
        ✅ 💼 LinkedIn — Your Company
        ✅ 🧵 Threads
        ✅ 🦋 Bluesky

      Binance Square ⏰ 23 May 2026 14:30 UTC
        📥 в очереди #42 (1 фото)

      ────────
      🖼 фото: 1
      «GM ☀️ сегодня катим новую фичу»


Ты ▸ /menu

Бот ▸ 👋 Buffer Poster Bot

      Активных каналов Buffer (4):
        🐦 @yourhandle
        💼 LinkedIn — Your Company
        🧵 Threads
        🦋 Bluesky

      Binance Square: ✅ активен
        в очереди: 12 постов
        следующий: 24 May 2026 09:15 UTC

      Расписание: случайно 1–240 ч
      📊 история: 47 ✅ / 2 ❌ (всего 49)

      [📡 Каналы] [📋 Очередь]
      [🪙 Binance Square] [📊 Логи]
      [⚙️ Настройки] [🔁 Обновить]
```

### Фичи

- **Случайное расписание** — каждый пост получает случайный `dueAt`. Настраивается через `SCHEDULE_MIN_HOURS` / `SCHEDULE_MAX_HOURS`.
- **Альбомы / карусели** — до 4 фото группируются в один Buffer-пост и один Binance Square image post.
- **Видео-посты** — пересланное видео (≤20 MB, лимит Bot API `getFile`) публикуется как нативный видео-пост Binance Square; Buffer получает текст + фото.
- **Защита от дублей** — MD5 хэш текста, блокирует повторные публикации.
- **Inline-меню + CRUD** — главное меню, переключение каналов, управление очередью (edit/delete/reschedule/send now).
- **Журнал публикаций** — `/logs` с историей успехов и фейлов, пагинацией, фильтром.
- **Binance Square v2 media flow** — официальная реализация из [binance/binance-skills-hub](https://github.com/binance/binance-skills-hub) (image upload, polling status, error codes `220003/220004/220009/220014/20002/20013/20022`).
- **Image hosting fallback chain** для Buffer — imgbb (primary) → catbox → 0x0.st.
- **Single-user lock** — `ALLOWED_USER_ID` гарантирует что инстансом пользуешься только ты.
- **Pause / resume** Binance scheduler + **batch flush** «опубликовать всё сейчас».
- **Zero infra** — SQLite на mounted volume, один worker процесс.

---

## Quick start

### 1. Получи токены

| Переменная | Где взять |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ALLOWED_USER_ID`    | [@userinfobot](https://t.me/userinfobot) — твой numeric Telegram ID |
| `BUFFER_ACCESS_TOKEN`| [publish.buffer.com/settings/api](https://publish.buffer.com/settings/api) → API (Beta) |
| `IMGBB_API_KEY`      | [api.imgbb.com](https://api.imgbb.com/) — бесплатный, рекомендую для надёжности |
| `BINANCE_SQUARE_API_KEY` | [Binance Skills Hub → square-post → Creator Center](https://www.binance.com/en/skills/detail/binance/square-post) (опционально) |

### 2. Запуск через Docker

```bash
git clone https://github.com/SMOService/buffer-poster-bot.git
cd buffer-poster-bot
cp .env.example .env
# заполни .env
docker compose up -d
```

### 3. Или локально

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export ALLOWED_USER_ID=...
export BUFFER_ACCESS_TOKEN=...
python bot.py
```

### 4. Или one-click на Railway

1. Сделай fork репо на GitHub.
2. Railway → **New Project** → **Deploy from GitHub** → выбери свой fork.
3. **Variables** → заполни env-переменные сверху + `SCHEDULE_MIN_HOURS=1`, `SCHEDULE_MAX_HOURS=240`.
4. **Volume** (обязательно, иначе данные сотрутся при redeploy):
   - Правая кнопка на canvas → **Volume** → service: `worker`, mount path: `/app/data`.
5. Railway подхватит `Procfile` и запустит как worker.

---

## Команды

| Команда | Что делает |
|---|---|
| `/start` или `/menu` | Главное меню с inline-кнопками |
| `/channels` | Включить/выключить каналы Buffer; *Обновить из Buffer* для пересинхронизации |
| `/queue`    | Счётчики постов в очередях Buffer + сводка Binance |
| `/binance`  | Очередь Binance Square с CRUD на каждом посте |
| `/logs`     | Пагинированный журнал публикаций |

**Чтобы опубликовать:** просто перешли (или отправь) сообщение в бота. Поддерживается: текст, фото, фото + подпись, альбом до 4 фото, видео до 20 MB (видео идёт в Binance Square; Buffer получает текст + фото).

---

## Как работает расписание

**Buffer.** При каждой пересылке бот генерирует случайный `dueAt` в окне `[SCHEDULE_MIN_HOURS, SCHEDULE_MAX_HOURS]` от *now* (default 1–240 ч). Пост создаётся через Buffer GraphQL с `mode: customScheduled`.

**Binance Square.** Посты хранятся в SQLite-очереди на mounted volume **вместе с Telegram `file_id`s** на прикреплённые фото. Фоновый scheduler тикает каждые 60 с, забирает посты с истёкшим `publish_at`, скачивает свежие байты из Telegram, прогоняет через официальный Binance v2 media flow, и шлёт тебе ссылку на опубликованный пост.

**Защита от дублей.** Перед планированием бот считает `md5(text.strip().lower())` и проверяет таблицу `published_hashes`. Повтор? Жёсткий блок + предупреждение.

**Альбомы.** Telegram доставляет элементы альбома как отдельные сообщения с общим `media_group_id`. Бот буферизует их 1.5 с и шлёт одним Buffer-постом + одним Binance Square image post (до 4 фото для совместимости с X carousel).

**Pause / batch.** Нужно заморозить публикации перед лончем? Тапни `⏸ Pause` на экране Binance. Хочешь слить очередь сразу (например для координированного дропа)? `⚡ Опубликовать всё сейчас` с подтверждением.

---

## Binance Square v2 media flow

Реализация повторяет [binance/binance-skills-hub](https://github.com/binance/binance-skills-hub) (`post-image.mjs`, `post-video.mjs`, `lib.mjs`). Отдельной OpenAPI-страницы на `developers.binance.com` Binance не выпустил — исходники skill'а = единственный канонический источник.

| Шаг | URL | Body |
|---|---|---|
| 1. Presign | фото: `POST /v2/…/image/presignedUrl` · видео: `POST /v2/…/video/preSign` | фото: `{"imageName":"<name>.<ext>"}` · видео: `{"fileName", "size"}` → `data.presignedUrl`, `data.fileTicket` |
| 2. Upload | `PUT <presignedUrl>` | raw bytes, `Content-Type: image/<ext>` или `video/<ext>` |
| 3. Status | `POST /bapi/composite/v2/public/pgc/openApi/image/imageStatus` | `{"fileTicket":...}` polling: фото 3с × 10, видео 5с × 36 → `status==1` |
| 4. Publish | `POST /bapi/composite/v1/public/pgc/openApi/content/add` | image post: `{"contentType":1, "bodyTextOnly", "imageList":[imageUrl, …]}` (до 4) · video post: `{"contentType":3, "fileTicket", "cover", "videoTimeSeconds", "isPublish":true}` (+ `bodyTextOnly` только при непустом тексте) |

Все JSON-запросы идут с `X-Square-OpenAPI-Key`, `Content-Type: application/json`, **`clienttype: binanceSkill`**.

Quirks, которые обрабатывает `services/binance.py`:
- HTTP 504 на `/content/add` трактуется как success без `post_id` (как в официальном helper).
- Известные коды ошибок (`220003/4/9/14`, `20002/13/22`, `220095`) переводятся в человекочитаемые записи и классифицируются (`permanent`/`auth`/`quota`/`transient`) для retry-политики.
- Daily limits: **100 постов/день**, **400 uploads/день** — превышение → quota-hold до 00:00 UTC, медиа сохраняются.
- **Фото** уходят в `content/add` как processed `imageUrl`; **видео** — как его `fileTicket`: разные ссылки, один upload-конвейер.

Article-mode (`contentType=2` со cover) — есть в SDK-слое (`publish_article`), но UI ещё не сделан, см. Roadmap.

---

## Архитектура

```
buffer-poster-bot/
├── bot.py              # entry point: init_db, загрузка каналов, start scheduler, polling
├── bot_instance.py     # aiogram Bot/Dispatcher singletons + download_telegram_file
├── config.py           # env + constants (BUFFER_API, BINANCE_API_V1/V2, …)
├── db.py               # sqlite + миграции через PRAGMA user_version (schema v5)
├── keyboards.py        # все builders InlineKeyboardMarkup
├── scheduler.py        # background Binance publisher (60s тик, pause-aware)
├── state.py            # FSM states (EditBinance.waiting_text)
├── services/
│   ├── buffer.py       # Buffer GraphQL: fetch_channels, create_post, count_scheduled_posts
│   ├── binance.py      # v1 text + v2 media flow (presignedUrl → PUT → imageStatus → content/add)
│   └── uploader.py     # imgbb (primary) / catbox / 0x0.st fallback chain для Buffer
├── handlers/
│   ├── menu.py         # /start, /menu, home / settings callbacks
│   ├── channels.py     # /channels + toggle + refresh
│   ├── queue.py        # /queue summary
│   ├── binance.py      # /binance + CRUD + pause/resume + flush
│   ├── logs.py         # /logs + пагинация + фильтр
│   ├── post.py         # handle_post (forward → Buffer + Binance queue; текст/фото/альбом/видео)
│   └── common.py       # is_me, fmt_ts, fmt_delta, preview, random_due_at
├── tests/              # pytest: миграции, видео-очередь, TTL, dedup, retry-переходы
├── Procfile            # Railway worker entrypoint
├── Dockerfile          # docker / docker-compose / Coolify / любой VPS
├── docker-compose.yml  # готовый с volume mount
├── .env.example        # все env vars документированы
├── pyproject.toml      # ruff config
└── .github/
    ├── workflows/ci.yml          # ruff + py_compile + import smoke + Docker build
    ├── ISSUE_TEMPLATE/           # шаблоны bug / feature
    └── PULL_REQUEST_TEMPLATE.md
```

### БД (SQLite, `/app/data/bot.db`, schema v5)

| Таблица | Назначение |
|---|---|
| `channels` | Кэш каналов Buffer: `id`, `name`, `service`, `enabled` |
| `binance_queue` | Pending посты: `text`, `image_urls` (JSON, imgbb), `image_file_ids` (JSON, Telegram), `video_file_id` / `video_cover_file_id` / `video_duration`, `content_type`, `title`, `publish_at`, `status` (`pending`/`published`/`dead`), `next_attempt_at`, `last_error`, `attempt_count` |
| `published_hashes` | MD5 каждого опубликованного поста (dedup) |
| `post_history` | Журнал: `kind` (buffer/binance), `service`, `status`, `text_preview`, `ext_id`, `ext_url`, `error` (ротация по `HISTORY_LIMIT`) |
| `kv` | Key-value хранилище для состояния scheduler'а (`binance_paused` etc.) |

Миграции выполняются инкрементально на старте через `PRAGMA user_version` — апгрейд с v1.x сохраняет данные.

---

## Configuration reference

См. [`.env.example`](.env.example) для полного аннотированного списка. Обязательные:

```env
TELEGRAM_BOT_TOKEN=123456789:AA...
ALLOWED_USER_ID=123456789
BUFFER_ACCESS_TOKEN=1/abc...
```

Опциональные:

```env
IMGBB_API_KEY=...                # рекомендую — primary image host для Buffer
BINANCE_SQUARE_API_KEY=...       # включает Binance Square
BINANCE_USE_IMAGES=1             # 1 = грузить медиа в Binance v2, 0 = text-only посты в Binance
BINANCE_IMAGE_FALLBACK_TEXT=0    # 1 = публиковать text-only при сбое upload (медиа теряется)
BINANCE_MAX_ATTEMPTS=6           # попыток до dead-letter
BINANCE_BACKOFF_BASE_SEC=300     # backoff: база 5 мин
BINANCE_BACKOFF_MAX_SEC=21600    # backoff: cap 6 ч
BINANCE_PUBLISHED_TTL_DAYS=30    # чистить опубликованные из очереди через N дней (0 = хранить)
SCHEDULE_MIN_HOURS=1             # default 1
SCHEDULE_MAX_HOURS=240           # default 240 (10 дней)
HISTORY_LIMIT=500                # ротация post_history
ECOSYSTEM_LINKS_ENABLED=1        # 0 = скрыть раздел «🚀 Ещё инструменты»
SUNSET_NOTICE=                   # текст плашки-объявления в /start (пусто = нет)
DB_PATH=/app/data/bot.db         # override только для local dev
```

---

## Добавить новую соцсеть

1. Подключи канал в Buffer: **Settings → Channels → Connect Channel**.
2. В боте: `/channels` → тапни **🔄 Обновить из Buffer**.
3. Новый канал появится в списке — тапни чтобы включить.

Всё что поддерживает Buffer (сейчас 9+ сетей) работает из коробки. Код менять не нужно.

---

## Roadmap

- 📰 **Article publishing UI** — surface `contentType=2` (long-form со cover) в forward-flow. SDK-хелпер `publish_article(title, body, cover)` уже есть; нужна команда `/article` или forward-prompt.
- 🪝 **Webhook mode** — сейчас только polling; webhook упростит zero-downtime деплои.
- 📹 **Большие видео** — потолок 20 MB задаёт Bot API `getFile`; локальный Bot API server поднимает его до 2 GB для self-host'еров, которым нужно.

---

## Экосистема — hosted-альтернативы

Не хочешь self-host? Та же команда делает готовые hosted-боты для владельцев каналов и контент-мейкеров:

| Бот | Что делает |
|---|---|
| [@SuperappAIbot](https://t.me/SuperappAIbot?start=ref_github) | Ведение каналов: кросспостинг, AI-композер, аналитика конкурентов («Радар») |
| [@BridgePostBot](https://t.me/BridgePostBot?start=ref_github) | Автоперенос постов из Telegram на внешние площадки с AI-рерайтом |
| [@ZavodClawbot](https://t.me/ZavodClawbot?start=ref_github) | AI-фабрика контента — генерация постов под нишу по расписанию |
| [@TonChatAIbot](https://t.me/TonChatAIbot?start=ref_github) | AI-ассистент для текстов, идей и разборов |
| [@SaveAsVideoFreebot](https://t.me/SaveAsVideoFreebot?start=ref_github) | Скачивание видео из соцсетей для репостов |
| [@StarsCashFlowbot](https://t.me/StarsCashFlowbot?start=ref_github) | Монетизация аудитории через Telegram Stars |

Open-source соседи в org **SMOService**:

- **[Cross-Post-Bridge-AI-bot](https://github.com/SMOService/Cross-Post-Bridge-AI-bot)** — мосты между твоими Telegram-каналами с AI-rewriting, переводом и cross-posting'ом.

---

## Contributing

PR welcome — см. [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports через [issue-шаблоны](https://github.com/SMOService/buffer-poster-bot/issues/new/choose). Security disclosures: [SECURITY.md](SECURITY.md).

---

## License

[MIT](LICENSE) © 2026 SMOService

## Acknowledgements

- [aiogram 3.x](https://docs.aiogram.dev/) — Telegram Bot framework
- [aiohttp](https://docs.aiohttp.org/) — async HTTP client
- [Buffer GraphQL API](https://developers.buffer.com) — scheduling backbone
- [Binance Skills Hub: square-post](https://github.com/binance/binance-skills-hub) — официальный reference для Binance Square OpenAPI
- imgbb, catbox.moe, 0x0.st — бесплатные image-хосты, без которых Buffer URL-only assets неудобны
