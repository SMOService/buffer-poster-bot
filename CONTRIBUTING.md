# Contributing

Thanks for taking a look — PRs and issues are very welcome.

## Reporting bugs

Use the [bug report template](https://github.com/SMOService/buffer-poster-bot/issues/new?template=bug.yml). Include:

- Bot version (`git rev-parse --short HEAD` or release tag)
- Python version and platform (Railway / Docker / bare VPS / macOS dev)
- The exact env vars **without secrets** (e.g. `SCHEDULE_MIN_HOURS=1, SCHEDULE_MAX_HOURS=240, IMGBB set: yes`)
- Stderr log around the failure (the bot logs every Buffer/Binance call)
- Reproduction steps

## Suggesting features

Use the [feature request template](https://github.com/SMOService/buffer-poster-bot/issues/new?template=feature.yml). The bot is a **single-user single-file tool by design** — features that need multi-tenancy, web UI, billing, or non-Buffer providers belong in the commercial fork (see README → Ecosystem). PRs that add a third image host, a new Buffer field, or fix a Buffer schema drift are highly welcome.

## Development setup

```bash
git clone https://github.com/SMOService/buffer-poster-bot.git
cd buffer-poster-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install ruff  # linter, also used in CI
cp .env.example .env
# fill .env with a TEST bot token, not your production one
python bot.py
```

## Code style

- Single file `bot.py` is intentional. Don't refactor it into modules without an issue agreeing on the split first.
- `ruff` config is permissive — CI runs `ruff check`. Run it locally before pushing: `ruff check .`
- Type hints encouraged where they clarify intent, not required everywhere.
- Logger calls use `%`-style formatting (`logger.info("foo %s", x)`) — keep that pattern.

## Pull requests

- Branch off `main`.
- One topic per PR. Small PRs get merged fast.
- Update `CHANGELOG.md` under `[Unreleased]`.
- If you change behaviour, update `README.md` and `README.ru.md` together.
- CI must pass.

## Security

For anything that looks like a security issue, see [SECURITY.md](SECURITY.md) — please **do not** open a public issue.

## License

By contributing you agree your work is licensed under MIT (same as the project).
