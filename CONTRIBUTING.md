# Contributing to `flights`

Thanks for your interest in improving `flights`! This is an unofficial,
pure-HTTP, multi-provider SDK + CLI for airline flight availability. Please read
the disclaimer in the [README](README.md#flights): **scrape gently, don't hammer
the APIs, and don't resell the data.** Contributions that add abusive request
patterns or bypass a carrier's Terms of Use will not be accepted.

## Development setup

Requires **Python ≥ 3.12**.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1            # PowerShell (Windows)
# source .venv/bin/activate           # bash/zsh (macOS/Linux)

python -m pip install -e ".[dev]"     # editable install + test/lint/type tooling
```

## The quality gate

CI runs the same four checks on every push (Python 3.12 **and** 3.13). Run them
locally before opening a pull request:

```powershell
python -m black --check .             # formatting (Black owns formatting)
python -m ruff check .                # linting (Ruff is the linter only)
python -m mypy src                    # static types (src/ is type-checked)
python -m pytest                      # tests (offline; no live airline traffic)
```

To auto-format and auto-fix:

```powershell
python -m black .
python -m ruff check --fix .
```

### Tooling decisions

- **Black** is the single formatter (line length 100). Ruff's formatter stays
  disabled to avoid a two-formatter tug-of-war.
- **Ruff** is the linter. The selected rule sets live in `pyproject.toml`
  (`[tool.ruff.lint]`).
- **mypy** type-checks `src/` (tests, examples, and `web/` are not type-checked).
- **Tests are fully offline.** Every airline request is mocked with the
  [`responses`](https://github.com/getsentry/responses) library, so the suite is
  deterministic and never touches a live carrier. Do not add tests that make
  real network calls.

## Adding a new airline provider

Every airline is a `flights.core.provider.BaseProvider` subclass that returns the
normalized `flights.core.models` shapes (`Airport`, `DayFare`, `Flight`). The
generic CLI and crawler are written entirely against that interface, so you only
implement the airline-specific parts.

1. Copy the template package `src/flights/providers/example/` to
   `src/flights/providers/<airline>/`.
2. Implement the four required methods — `origins`, `destinations`,
   `lowfare_window`, `flights` — with real HTTP calls. See
   `src/flights/providers/frontier/client.py` for a complete real implementation
   (auth, GraphQL, retries, thread-safety).
3. Register it in `src/flights/providers/__init__.py`:

   ```python
   from .<airline> import <Airline>Provider

   register_provider("<airline>", <Airline>Provider)
   ```

4. Add offline tests under `tests/` using the `responses` mock (mirror
   `tests/test_provider.py`).
5. Verify: `python -m flights.cli routes --provider <airline>`.

Persist crawl data only through `flights.core.storage` (its DDL, column tuples,
and row adapters) so your provider's rows stay compatible with the web exporter
and analysis scripts.

## Commits and pull requests

- Keep each commit focused and its message in the imperative mood
  (`fix: …`, `feat: …`, `refactor: …`, `test: …`, `build: …`, `ci: …`).
- Preserve behavior parity unless a change is explicitly a behavior change: the
  export JSON shape, CLI flags/output, and the web UX should stay identical.
- Update `CHANGELOG.md` (the `Unreleased` section) for user-facing changes.
- Make sure the full quality gate passes before requesting review.

## Reporting issues

Undocumented endpoints change without notice. When filing a bug, include the
provider, the exact CLI command or code, and the full error output.
