# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Offline test suite (`tests/`) with a `responses`-backed harness, core/model,
  provider, crawler, export, storage, CLI, and schema-contract tests.
- Continuous integration (`.github/workflows/ci.yml`) running Black, Ruff, mypy,
  and pytest on a Python 3.12 / 3.13 matrix.
- `flights.core.storage`: a single source of truth for the crawl SQLite schema
  (DDL, column order, migration, the shared cheapest-cash SQL, and row adapters).
- `flights.core.models.cheapest_cash()` helper shared by the models and CLI.
- Example provider template package (`flights.providers.example`) plus
  `CONTRIBUTING.md`, `CHANGELOG.md`, and `.editorconfig`.

### Changed
- Minimum supported Python is now 3.12.
- The crawler, web exporter, and analysis example now import the schema from
  `flights.core.storage` instead of hand-typed column lists, so a schema change
  can no longer silently drift away from a consumer's query (fixes the web
  pipeline "missing column" class of bug).
- `web/build_data.py` no longer hard-codes a machine-specific default DB path;
  it reads `$FLIGHTS_DB` or falls back to a relative `us_lowfares.db`.
- Packaging: the version and the `requests` dependency are each declared once in
  `pyproject.toml` (`__version__` is read from installed metadata;
  `requirements.txt` installs the project itself).

### Fixed
- Replaced the deprecated `datetime.utcnow()` in the crawler with timezone-aware
  `datetime.now(datetime.UTC)`.

## [0.2.0]

- Multi-provider architecture: a provider-agnostic core (models, interface,
  registry, crawler) with Frontier as the first bundled airline, a `flights`
  CLI, and an offline D3 web fare map.
