# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/SMOService/buffer-poster-bot/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/SMOService/buffer-poster-bot/releases/tag/v1.3.0
