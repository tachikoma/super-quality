# fix-dart-api-key-propagation - Work Plan

## TL;DR (For humans)

**What you'll get:** `--dart-api-key YOUR_KEY` CLI flag actually works. The key you pass on the command line is forwarded to the OpenDartReader data loader, so financial data fetching no longer fails with "DART_API_KEY is not set."

**Why this approach:** Instead of silently mutating global environment variables, we use clean parameter passing — the API key is explicitly forwarded from the CLI handler to the data-fetching functions that need it. Simple, testable, no side effects.

**What it will NOT do:** Not change how `.env` files or environment variables work. Not touch the backtest engine or strategy logic (they already get the config correctly). Not add new features or tests.

**Effort:** Quick
**Risk:** Low — purely mechanical plumbing fix across 2 files, 3 function signatures, 2 call sites.
**Decisions to sanity-check:** Whether we should also fix `get_shares_outstanding()` (dead code, same anti-pattern — included for consistency).

Your next move: approve to execute the plan, or ask for a high-accuracy review before approving.

---

> TL;DR (machine): Quick | Low | `get_financial_data()` and `get_shares_outstanding()` in `loader.py` create their own `SuperQualityConfig()` without forwarding the `--dart-api-key` CLI arg. Fix: add optional `api_key` param, pass from `_cmd_run()`.

## Scope
### Must have
- Fix `get_financial_data()` to accept and use an optional `api_key` parameter
- Update `_cmd_run()` in `main.py` to forward the API key to `get_financial_data()`
- Fix `get_shares_outstanding()` with the same pattern (latent bug, currently dead code)

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No `os.environ` mutation
- No changes to `SuperQualityConfig` class, `.env` loading, or pydantic-settings behavior
- No changes to `BacktestEngine`, strategy, factor, or analysis code
- No new tests — purely mechanical plumbing; existing test coverage verifies

## Verification strategy
- Test decision: tests-after (run existing test suite to confirm no regression)
- Evidence: `pytest -v` passes; manual trace of `--dart-api-key` → `get_financial_data` path

## Execution strategy
### Parallel execution waves
Wave 1: 2 parallel changes (loader.py signatures + main.py call site), then 1 verification step.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. Fix loader.py | — | 2 | — |
| 2. Fix main.py | — | 3 | 1 |
| 3. Verify | 1, 2 | — | — |

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. `src/super_quality/data/loader.py`: Add optional `api_key` param to `get_financial_data` and `get_shares_outstanding`
  What to do / Must NOT do:
    - `get_financial_data(tickers, years, api_key=None)`: if `api_key` is provided (non-None, non-empty), use it directly instead of creating a `SuperQualityConfig()`. If not provided, fall back to the existing `SuperQualityConfig()` logic (backward compatible).
    - `get_shares_outstanding(ticker, dates, api_key=None)`: same pattern — accept optional `api_key`, use it when given.
    - Must NOT mutate `os.environ` or the config object.
    - Must NOT change behavior when `api_key` is not passed (preserve existing fallback).
  Parallelization: Wave 1 | Blocked by: — | Blocks: 3
  References:
    - `src/super_quality/data/loader.py:291-328` (get_financial_data — current impl)
    - `src/super_quality/data/loader.py:581-665` (get_shares_outstanding — current impl)
    - `src/super_quality/data/loader.py:319-328` (the bug: creates SuperQualityConfig() with no args)
    - `src/super_quality/data/loader.py:604-607` (same bug in get_shares_outstanding)
  Acceptance criteria:
    - `get_financial_data(["000660"], [2024], api_key="test_key")` does NOT raise ValueError about missing key
    - `get_financial_data(["000660"], [2024])` (no api_key) still falls back to existing behavior
  QA scenarios: happy: trace the code path to confirm `api_key` reaches the `OpenDartReader()` constructor. failure: function raises `ValueError` when `api_key` is `None` and `DART_API_KEY` env/`.env` is absent.
  Commit: Y | `fix(data): forward CLI --dart-api-key to get_financial_data and get_shares_outstanding`

- [x] 2. `src/super_quality/main.py`: Pass API key from `_cmd_run` to `get_financial_data()`
  What to do / Must NOT do:
    - In `_cmd_run()`, capture the resolved `api_key` (from CLI args or env) and pass it to `get_financial_data(tickers[:50], years, api_key=api_key)`.
    - Must NOT pass `api_key` to other loader functions (they don't need it).
    - Must NOT change other behavior.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 3
  References:
    - `src/super_quality/main.py:82-146` (_cmd_run function)
    - `src/super_quality/main.py:125` (the call site: `get_financial_data(tickers[:50], years)`)
    - `src/super_quality/main.py:85` (api_key resolution: `args.dart_api_key or os.environ.get(...)`)
  Acceptance criteria:
    - `get_financial_data` is called with `api_key` keyword argument
    - No other function signature change in main.py
  QA scenarios: happy: string search confirms `api_key=` is present at the call. failure: no change to env or .env behavior.
  Commit: N (included in todo 1 commit)

- [x] 3. Verify no regressions
  What to do / Must NOT do:
    - Run `pytest -v` to confirm all existing tests pass.
    - Run `ruff check src/super_quality/` to confirm no lint violations.
    - Run `cd src && python -c "from super_quality.data.loader import get_financial_data; print('import OK')"` to confirm import works.
    - Must NOT change any test files.
  Parallelization: Wave 2 | Blocked by: todos 1, 2 | Blocks: —
  References: `tests/` directory
  Acceptance criteria: `pytest -v` passes 100%, `ruff check` passes.
  QA scenarios: happy: all tests pass. failure: fix any test breakage.
  Commit: N (squash into todo 1 commit if any fix needed)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — all 3 todos implemented exactly as specified ✅
- [x] F2. Code quality review — `ruff check` passes ✅
- [ ] F3. Real manual QA — TO BE DONE BY USER: run `uv run super-quality run --dart-api-key YOUR_KEY --start 2015-01-01 --end 2015-03-01` with a real key to verify no "DART_API_KEY is not set" error
- [x] F4. Scope fidelity — changes only in `loader.py` and `main.py`, confirmed ✅

## Commit strategy
Single commit after all todos: `fix(data): forward CLI --dart-api-key to get_financial_data and get_shares_outstanding`

## Success criteria
1. `uv run super-quality run --dart-api-key <real_key> --start 2015-01-01 --end 2015-03-01` no longer fails with "DART_API_KEY is not set"
2. Existing tests pass
3. No lint violations
