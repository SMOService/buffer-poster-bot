# Buffer Poster Bot

[![CI](https://github.com/SMOService/buffer-poster-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/SMOService/buffer-poster-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.x-blueviolet)](https://docs.aiogram.dev/)

> Self-hosted Telegram-бот: пересылаешь посты — он планирует их со случайным временем публикации (от 1 до 240 часов) в **Buffer** (X, LinkedIn, Threads, Facebook, Instagram, …) и **Binance Square**. Закинул 50 постов — получаешь ~10 дней контента, который сам распределится по всем соцсетям.

[English version](README.md)

---

## Что делает

Пересылаешь пост в бота — бот выбирает случайный `dueAt` в окне `1–240 ч` и планирует публикацию на всех включённых каналах Buffer + Binance Square. Один drop из 50 постов = месяц контента вперёд.

```
форвард в Telegram  →  случайное расписание (1–240ч)  →  Buffer  →  X / LinkedIn / Threads / FB / IG / …
                                                       →  Binance Square (отдельная очередь)
```

### Фичи

- **Случайное расписание** — каждый форварднутый пост получает рандомный `dueAt`. Окно настраивается через `SCHEDULE_MIN_HOURS` / `SCHEDULE_MAX_HOURS`.
- **Карусели** — альбом до 4 фото группируется в один пост Buffer (совместимо с X-каруселями).
- **Защита от дублей** — MD5 от тела поста, повторный форвард того же текста блокируется.
- **Управление каналами через UI** — `/channels` с инлайн-кнопками, синхронизация с Buffer одним тапом.
- **Binance Square** — отдельная очередь, фоновый scheduler, `/binance` для инспекции, ручной *Send Now*.
- **Fallback-цепочка хостингов картинок** — imgbb (основной) → catbox → 0x0.st. Buffer требует публичные URL — бот это берёт на себя.
- **Single-user lock** — только владелец `ALLOWED_USER_ID` может пользоваться своим инстансом.
- **Без инфры** — SQLite на mounted volume, один worker-процесс.

---

## Быстрый старт

### 1. Получи токены

| Переменная | Где взять |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ALLOWED_USER_ID`    | [@userinfobot](https://t.me/userinfobot) — твой Telegram ID |
| `BUFFER_ACCESS_TOKEN`| [publish.buffer.com/settings/api](https://publish.buffer.com/settings/api) → API (Beta) |
| `IMGBB_API_KEY`      | [api.imgbb.com](https://api.imgbb.com/) — бесплатно, рекомендуется |
| `BINANCE_SQUARE_API_KEY` | Binance Square Creator Center (опционально) |

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

### 4. Или один клик на Railway

1. Форкни репо на GitHub.
2. Railway → **New Project** → **Deploy from GitHub** → выбери свой форк.
3. Вкладка **Variables** → заполни 5 переменных + `SCHEDULE_MIN_HOURS=1`, `SCHEDULE_MAX_HOURS=240`.
4. **Volume** (обязательно — иначе данные стираются при редеплое):
   - Правая кнопка по канвасу → **Volume** → service: `worker`, mount path: `/app/data`.
5. Railway автоматически подхватит `Procfile` и запустит как worker.

---

## Команды

| Команда | Описание |
|---|---|
| `/start`    | Статус: активные каналы, размер очереди Binance, следующая публикация |
| `/channels` | Включить/выключить каналы Buffer; *Обновить из Buffer* для синка |
| `/queue`    | Количество запланированных постов по каждому каналу Buffer + очередь Binance |
| `/binance`  | Следующие 10 постов Binance Square с временем + ручной *Send Now* |

**Чтобы опубликовать:** просто переслать (или отправить) сообщение боту. Поддерживается: текст, фото, фото+подпись, альбом до 4 фото.

---

## Как работает расписание

**Buffer.** При каждой пересылке бот генерирует случайный `dueAt` в окне `[SCHEDULE_MIN_HOURS, SCHEDULE_MAX_HOURS]` от текущего момента (по умолчанию 1–240 ч). Пост создаётся через Buffer GraphQL с `mode: customScheduled` — Buffer публикует в назначенное время.

**Binance Square.** Посты хранятся в SQLite-очереди на volume. Фоновый scheduler тикает каждые 60 с, находит посты с истёкшим `publish_at`, дёргает Binance Square OpenAPI, и присылает в ЛС уведомление со ссылкой на опубликованный пост.

**Защита от дублей.** Перед планированием бот считает `md5(text.strip().lower())` и сверяет с таблицей `published_hashes`. Повтор? Хард-блок + предупреждение.

**Альбомы.** Telegram доставляет элементы альбома отдельными сообщениями с одним `media_group_id`. Бот буферизует их 1.5 с, затем отправляет одним постом Buffer (до 4 фото — совместимо с X-каруселями).

---

## Архитектура

```
buffer-poster-bot/
├── bot.py              # всё в одном файле (745 LOC, by design)
├── requirements.txt    # aiogram 3.x + aiohttp
├── Procfile            # Railway worker entrypoint
├── Dockerfile          # docker / docker-compose / Coolify / любой VPS
├── docker-compose.yml  # готовый compose с volume
├── .env.example        # все env-переменные с пояснениями
└── .github/
    ├── workflows/ci.yml          # ruff + py_compile + Docker build
    ├── ISSUE_TEMPLATE/           # bug / feature шаблоны
    └── PULL_REQUEST_TEMPLATE.md
```

Бот специально единый файл. Он маленький — читается за 20 минут, форкается, модифицируется под свой стек. Никаких лишних абстракций.

### База данных (SQLite, на `/app/data/bot.db`)

| Таблица | Назначение |
|---|---|
| `channels` | Кэш каналов Buffer: `id`, `name`, `service`, `enabled` |
| `binance_queue` | Очередь Binance Square: `text`, `image_url`, `publish_at`, `published` |
| `published_hashes` | MD5 каждого успешно запланированного текста (защита от дублей) |

---

## Справочник конфигурации

Полный аннотированный список — в [`.env.example`](.env.example). Обязательные:

```env
TELEGRAM_BOT_TOKEN=123456789:AA...
ALLOWED_USER_ID=123456789
BUFFER_ACCESS_TOKEN=1/abc...
```

Опциональные:

```env
IMGBB_API_KEY=...                # рекомендуется — основной хост картинок
BINANCE_SQUARE_API_KEY=...       # включает Binance Square
SCHEDULE_MIN_HOURS=1             # по умолчанию 1 (один час)
SCHEDULE_MAX_HOURS=240           # по умолчанию 240 (десять дней)
DB_PATH=/app/data/bot.db         # переопределять только для локальной разработки
```

---

## Добавить новую соцсеть

1. Подключи канал в Buffer: **Settings → Channels → Connect Channel**.
2. В боте: `/channels` → тапни **🔄 Обновить из Buffer**.
3. Новый канал появится в списке — включи его.

Всё, что поддерживает Buffer (сейчас 9+ соцсетей), работает из коробки. Менять код не нужно.

---

## Экосистема

Часть posting-стека **SMOService**:

- **[Cross-Post-Bridge-AI-bot](https://github.com/SMOService/Cross-Post-Bridge-AI-bot)** — мосты между Telegram-каналами с AI-рерайтом, переводом и кросспостингом.

Если нужна **коммерческая multi-tenant** версия (много проектов, оплата через Stars, Mini App, Buffer + Upload-Post + Postmypost) — в разработке. Следи за org.

---

## Контрибуция

PR приветствуются — см. [CONTRIBUTING.md](CONTRIBUTING.md). Баги через [шаблоны issue](https://github.com/SMOService/buffer-poster-bot/issues/new/choose). Уязвимости: [SECURITY.md](SECURITY.md).

---

## Лицензия

[MIT](LICENSE) © 2026 SMOService

## Спасибо

- [aiogram 3.x](https://docs.aiogram.dev/) — Telegram Bot framework
- [aiohttp](https://docs.aiohttp.org/) — асинхронный HTTP-клиент
- [Buffer GraphQL API](https://developers.buffer.com) — основа планирования
- [Binance Square OpenAPI](https://www.binance.com/en/skills/detail/binance/square-post) — Web3-дистрибуция
- imgbb, catbox.moe, 0x0.st — бесплатные хосты картинок, делающие URL-only assets Buffer работоспособными
