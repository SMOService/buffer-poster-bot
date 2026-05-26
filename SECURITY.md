# Security Policy

## Supported versions

Only the latest minor release on `main` receives fixes. Older tags are kept for reference.

| Version | Supported |
|---------|-----------|
| 1.3.x   | ✅        |
| < 1.3   | ❌        |

## Reporting a vulnerability

**Do not open a public GitHub issue.** Email **support@smoservice.media** with:

- A description of the issue and its impact
- Reproduction steps (a minimal proof of concept is ideal)
- Affected version / commit
- Your suggested mitigation, if any

We'll acknowledge within **72 hours** and aim to land a fix or coordinated disclosure within **14 days** for confirmed issues. Researchers who disclose responsibly will be credited in the release notes (unless they prefer to remain anonymous).

## Hardening tips for self-hosters

- **Treat `ALLOWED_USER_ID` as a soft auth gate, not a hard one.** Anyone who gets your bot token can impersonate you to Telegram. Rotate `TELEGRAM_BOT_TOKEN` via [@BotFather](https://t.me/BotFather) → `/revoke` if compromised.
- **Don't commit `.env`.** It's in `.gitignore` for a reason. Use Railway / Coolify / Docker secrets instead.
- **Buffer access token has full publish rights** to every channel you've connected in Buffer. Treat it like a password.
- **The SQLite DB at `/app/data/bot.db` contains post bodies and channel metadata** (no Buffer/Binance secrets). Still — back it up with care and don't expose the volume publicly.
- **`IMGBB_API_KEY`, `BINANCE_SQUARE_API_KEY`** — same hygiene.

## Out of scope

- Telegram Bot API abuse via the legitimate bot token holder
- Buffer / Binance Square API limits, rate limits, and quirks (those are upstream)
- Image hosts (imgbb, catbox, 0x0) — they're third-party fallbacks, audit their TOS yourself if it matters
