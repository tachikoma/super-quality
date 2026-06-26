# incremental-price-cache - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 한 번 가격 데이터를 다운로드하면 영구히 캐시되어, 이후 날짜 범위를 넓히면 이미 받은 기간은 다시 받지 않고 추가된 기간만 다운로드합니다. 이전에 일부만 받은 연도(예: 2015년 6월~12월만 캐시된 상태에서 2015년 1월부터 요청)도 자동 감지하여 누락된 일자만 보충합니다.

**Why this approach:** 전체 데이터를 한 번에 받아 연도별로 나누어 저장하면 최초 실행 속도는 그대로 유지하면서, 이후 실행은 누락된 연도만 빠르게 채울 수 있습니다. 연도별 메타데이터에 실제 데이터 범위(min_date/max_date)를 기록하여 부분 커버리지를 정확히 추적합니다.

**What it will NOT do:** 새로 상장된 종목의 과거 데이터를 소급해서 채우지 않습니다. 기존 캐시 파일을 자동으로 옮기거나 삭제하지 않습니다.

**Effort:** Medium
**Risk:** Low — 기존 동작을 유지하며 캐시 전략만 변경, 변경 범위가 `loader.py` + `cache.py` 2개 파일로 제한됨
**Decisions to sanity-check:** 연도별 Parquet 파일 스키마 일관성, 메타데이터 JSON 구조 (years dict에 min_date/max_date 포함)

Your next move: **Approve this plan** → 실행. Full execution detail follows below.

---

> TL;DR (machine): Medium / Low / `DataCache` JSON metadata + `get_price_data()` yearly-incremental caching

## Scope
### Must have
- `DataCache.put_json()` / `DataCache.get_json()` for lightweight metadata (cached-years list)
- `get_price_data()` refactored into three phases:
  1. **Discovery** — compute year range, load metadata, validate per-year date coverage, identify missing years and partially-covered years
  2. **Download** — first-run: full range → split by year; incremental: only missing or partially-covered years
  3. **Assembly** — load yearly Parquet files, concat, filter by requested range, return
- Metadata schema with per-year `min_date`/`max_date` for detecting partial coverage
- `_download_ticker_batch(tickers, start, end, shares_map) -> pd.DataFrame` helper extracted from current loop
- Progress logging per year (incremental) and per 50 tickers (within each download batch)
- Empty-year handling: if a year produces zero rows, log warning and skip cache
- Existing `prices_*` cache keys: log debug message if found (no migration)

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No concurrent/parallel download — stays sequential with existing 0.05s delay
- No per-ticker cache files — only per-year + metadata files
- No backfill for new/delisted tickers into old yearly caches
- No changes to `get_financial_data()`, `get_kosdaq_index()`, or `get_retail_net_buy()`
- No changes to `main.py`, `config.py`, or backtest engine
- No cache locking (single-process assumption)
- No schema versioning beyond a simple `cache_version` int in metadata JSON

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + manual `uv run super-quality run` smoke test
- Evidence: `.omo/evidence/task-<N>-incremental-price-cache.` — log output + parquet file existence check

## Execution strategy
### Parallel execution waves
- Wave 1: Todo 1 (DataCache JSON) + Todo 2 (helper extract) — independent
- Wave 2: Todo 3 (main incremental logic) — blocked on Todo 1 & 2
- Wave 3: Todo 4 (smoke test) — blocked on Todo 3

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. DataCache JSON metadata | — | 3 | 2 |
| 2. Extract `_download_ticker_batch` helper | — | 3 | 1 |
| 3. Incremental cache logic in `get_price_data()` | 1, 2 | 4 | — |
| 4. Smoke test & verification | 3 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Add JSON metadata methods to `DataCache`
  What to do / Must NOT do:
    - Add `put_json(key: str, data: dict | list) -> None` — writes `{cache_dir}/{key}.json` via `json.dumps`
    - Add `get_json(key: str) -> dict | list | None` — reads and returns parsed JSON, or `None` if file missing
    - Must NOT change existing `get`/`put`/`exists`/`clear` signatures or behavior
    - Must NOT depend on pandas (json stdlib only)
  Parallelization: Wave 1 | Blocked by: — | Blocks: 3
  References:
    - `src/super_quality/data/cache.py:20-84` (existing DataCache methods, same pattern)
    - Cache dir: `data/raw/` (already established, `cache.py:29`)
  Acceptance criteria (agent-executable):
    1. `uv run python -c "from super_quality.data.cache import DataCache; c=DataCache('/tmp/test_cache'); c.put_json('test', {'a':1}); assert c.get_json('test') == {'a':1}"` returns without error
    2. `c.get_json('nonexistent')` returns `None`
  QA scenarios: happy (round-trip dict), sad (missing file → None), edge (empty dict, list, nested). Evidence in `.omo/evidence/task-1-incremental-price-cache.json`.
  Commit: Y | `feat(cache): add put_json/get_json for lightweight metadata`

- [x] 2. Extract `_download_ticker_batch()` helper from `get_price_data()`
  What to do / Must NOT do:
    - Extract lines 152-215 (the per-ticker loop body: download → normalize → compute mcap → build DataFrame) into:
      ```python
      def _download_ticker_batch(
          tickers: list[str],
          start: str | date,
          end: str | date,
          shares_map: dict[str, float],
      ) -> pd.DataFrame:
      ```
    - The helper must accept `shares_map` (pre-computed from `get_krx_listings()`)
    - It must handle progress logging (50-ticker interval) and 0.05s delay internally
    - Return: DataFrame with columns `[ticker, date, open, high, low, close, volume, mcap]` (date as column, not index)
    - Must NOT touch cache (`_cache`); pure download+transform
    - The outer `get_price_data()` calls `get_krx_listings()` and computes `shares_map` BEFORE calling the helper
    - Existing `get_price_data()` loop body is replaced with a single call to this helper
  Parallelization: Wave 1 | Blocked by: — | Blocks: 3
  References:
    - `src/super_quality/data/loader.py:152-215` (existing loop body)
    - `src/super_quality/data/loader.py:142-150` (shares_map computation, stays in get_price_data)
  Acceptance criteria (agent-executable):
    1. `uv run python -c "from super_quality.data.loader import _download_ticker_batch; help(_download_ticker_batch)"` shows the function exists
    2. `uv run python -m pytest tests/ -x -q --no-header 2>/dev/null || echo 'no tests yet'` — at minimum, module imports without error
    3. **Behavioral parity**: write a temporary script that calls both the old inline loop (keep it side-by-side during extraction) and the new `_download_ticker_batch` with 3 test tickers over 1 month, then `pd.testing.assert_frame_equal(old_result, new_result)` passes
  QA scenarios:
    - Happy: function signature matches spec, imports cleanly
    - Behavioral parity: inline loop output == helper output (same columns, dtypes, row count)
    - Edge: empty ticker list → returns empty DataFrame with correct schema
    - Edge: all tickers fail → returns empty DataFrame (not crash)
    Evidence: `.omo/evidence/task-2-incremental-price-cache/` — comparison script output + assert_frame_equal result.
  Commit: Y | `refactor(loader): extract _download_ticker_batch helper from get_price_data`

- [x] 3. Implement yearly incremental caching in `get_price_data()`
  What to do / Must NOT do:
    - **Metadata schema** (`price_meta.json`) — **사용자가 요청한 날짜** 저장 (실제 데이터의 min/max가 아님):
      ```json
      {
        "cache_version": 1,
        "years": {
          "2015": {"req_start": "2015-07-01", "req_end": "2015-12-31"},
          "2016": {"req_start": "2016-01-01", "req_end": "2016-12-31"}
        }
      }
      ```
      - `req_start` / `req_end` = `_download_ticker_batch()`에 전달한 요청 날짜 범위 (사용자 입력 기준)
      - 휴장일(주말/공휴일) 문제 방지: 사용자가 `--start 2025-01-01`로 요청하면 `req_start`는 `"2025-01-01"` (데이터가 1월 2일부터여도 상관없음)
      - 비교: `info["req_start"] > y_start` or `info["req_end"] < y_end` → reload 필요
    - Full rewritten `get_price_data()` flow:
      ```
      ┌──────────────────────────────────────────────┐
      │ 1. Discovery                                 │
      │   start_date = _to_date(start)                │
      │   end_date = _to_date(end)                    │
      │   years_needed = {str(y) for y in             │
      │     range(start_date.year, end_date.year+1)}  │
      │                                              │
      │   meta = _cache.get_json("price_meta")       │
      │   if meta is None:                            │
      │       meta = {"cache_version": 1, "years": {}}│
      │                                              │
      │   download_years = set()  # 전혀 없음          │
      │   reload_years = set()    # 부분만 있음         │
      │   for y_str in sorted(years_needed):           │
      │       y = int(y_str)                           │
      │       y_start = max(start_date, date(y,1,1))   │
      │       y_end = min(end_date, date(y,12,31))     │
      │       if y_str not in meta["years"]:           │
      │           download_years.add(y_str)            │
      │       else:                                    │
      │           info = meta["years"][y_str]          │
      │           if info["min_date"] > y_start or     │
      │              info["max_date"] < y_end:         │
      │               reload_years.add(y_str)          │
      └──────────────────────┬───────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                              ▼
      ┌─────────────────┐     ┌─────────────────────────┐
      │ 2a. First run   │     │ 2b. Incremental          │
      │ download_years   │     │ download_years |          │
      │ == years_needed  │     │ reload_years 있음         │
      │                 │     │                         │
      │ Download FULL   │     │ For reload_years:       │
      │ range at once   │     │   download FULL year     │
      │ → split by year │     │   (Jan 1 ~ Dec 31)      │
      │ → save yearly   │     │   → overwrite cache     │
      │                 │     │                         │
      │                 │     │ For download_years:     │
      │                 │     │   download only         │
      │                 │     │   requested range       │
      │                 │     │   → save                │
      └────────┬────────┘     └───────────┬─────────────┘
               │                          │
               └──────────┬───────────────┘
                          ▼
      ┌──────────────────────────────────────────────┐
      │ 3. Assembly                                   │
      │   Load price_{year} for each year_needed      │
      │   pd.concat → sort_index                      │
      │   Filter: [start_date, end_date]              │
      │   Return MultiIndex (ticker, date)             │
      └──────────────────────────────────────────────┘
      ```
    - **Detailed pseudocode**:
      ```python
      def get_price_data(tickers, start, end):
          import FinanceDataReader as fdr
          start_date = _to_date(start)
          end_date = _to_date(end)
          
          # 캐시 키 (고정 — 더 이상 날짜 범위를 키에 포함하지 않음)
          cache_key_prices = "prices_v2"  # v2 캐시 식별자
          
          # 1. Discovery
          listing = get_krx_listings()
          shares_map = _build_shares_map(listing)
          
          years_needed = {str(y) for y in range(start_date.year, end_date.year + 1)}
          
          meta = _cache.get_json("price_meta")
          if meta is None:
              meta = {"cache_version": 1, "years": {}}
          
          download_years: set[str] = set()
          reload_years: set[str] = set()
          
          for y_str in sorted(years_needed):
              y = int(y_str)
              y_start = max(start_date, date(y, 1, 1))
              y_end = min(end_date, date(y, 12, 31))
              
               if y_str not in meta["years"]:
                   download_years.add(y_str)
               else:
                   info = meta["years"][y_str]
                   # req_start/req_end = 사용자가 그 연도에 이전에 요청했던 범위
                   # y_start/y_end = 이번 요청의 해당 연도 범위
                   # 캐시가 요청 범위를 완전히 커버하지 않으면 reload
                   if info["req_start"] > y_start or info["req_end"] < y_end:
                       reload_years.add(y_str)
          
          # 2. Download phase
          if download_years == years_needed:
              # First download: full range at once → split by year
              logger.info("전체 범위 다운로드 중… (%d년)", len(years_needed))
              full = _download_ticker_batch(tickers, start_date, end_date, shares_map)
              if not full.empty:
                  full["_year"] = full["date"].dt.year.astype(str)
                  for y_str, grp in full.groupby("_year"):
                      grp = grp.drop(columns=["_year"])
                      _cache.put(f"price_{y_str}", grp)
                      y = int(y_str)
                      # req_start/req_end = 실제로 다운로드한 요청 범위
                      y_req_start = max(start_date, date(y, 1, 1))
                      y_req_end = min(end_date, date(y, 12, 31))
                      meta["years"][y_str] = {
                          "req_start": y_req_start.isoformat(),
                          "req_end": y_req_end.isoformat(),
                      }
                      logger.info("  %s → %s일 캐시됨", y_str, len(grp))
                  del full
          else:
              # Incremental: download missing / incomplete years
              years_to_fetch = sorted(download_years | reload_years)
              for idx, y_str in enumerate(years_to_fetch):
                  y = int(y_str)
                  if y_str in reload_years:
                      # 부분 커버 → 전체 연도 재다운로드
                      y_start = date(y, 1, 1)
                      y_end = date(y, 12, 31)
                      reason = "재다운로드 (부분 캐시)"
                  else:
                      y_start = max(start_date, date(y, 1, 1))
                      y_end = min(end_date, date(y, 12, 31))
                      reason = "신규"
                  
                  logger.info("  %s 다운로드 중… %s (%s ~ %s)",
                              y_str, reason, y_start, y_end)
                  year_data = _download_ticker_batch(
                      tickers, y_start, y_end, shares_map
                  )
                  if year_data.empty:
                      logger.warning("  %s: 데이터 없음, 건너뜀", y_str)
                      continue
                   _cache.put(f"price_{y_str}", year_data)
                   meta["years"][y_str] = {
                       "req_start": y_start.isoformat(),
                       "req_end": y_end.isoformat(),
                   }
          
          # 캐시 적중 로그 (다운로드가 전혀 없었을 때)
          if not download_years and not reload_years:
              logger.info("캐시 적중: %s — 모든 데이터가 캐시되어 있습니다",
                          ", ".join(sorted(years_needed)))
          
          # 메타데이터 저장
          _cache.put_json("price_meta", meta)
          
          # 3. Assembly
          frames = []
          for y_str in sorted(years_needed):
              df = _cache.get(f"price_{y_str}")
              if df is not None:
                  frames.append(df)
          
          if not frames:
              result = pd.DataFrame(
                  columns=["ticker", "date", "open", "high", "low",
                           "close", "volume", "mcap"],
              )
              result = result.set_index(["ticker", "date"])
          else:
              result = pd.concat(frames, ignore_index=True)
              result["date"] = pd.to_datetime(result["date"])
              result = result.set_index(["ticker", "date"]).sort_index()
              # 요청 범위로 필터링 (캐시에 더 넓은 범위가 있을 수 있음)
              result = result.loc[
                  (slice(None), slice(start_date, end_date)), :
              ]
          
          return result
      ```
    - **Metadata 저장 원칙**: `req_start`/`req_end`는 **실제로 FinanceDataReader에 전달한 날짜**이므로, 비개장일을 포함한 사용자 요청 범위 그대로 저장됨. 데이터 자체의 `min`/`max`가 아님.
    - Must NOT re-download a year if it's fully cached and covers the requested range
    - Must NOT download the same ticker twice (reuses `_download_ticker_batch`)
    - Must preserve existing return type: MultiIndex `(ticker, date)` with columns `[open, high, low, close, volume, mcap]`
    - Must preserve existing progress logging + 0.05s delay (inside `_download_ticker_batch`)
    - All date comparisons MUST use `_to_date()` first (never compare `str` to `date`)
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 4
  References:
    - `src/super_quality/data/loader.py:106-237` (full existing get_price_data)
    - `src/super_quality/data/loader.py:136` (old cache key pattern — to replace)
    - `src/super_quality/data/loader.py:222-232` (concat + sort index pattern — to keep)
    - `src/super_quality/data/loader.py:28-32` (_to_date / _to_str helpers)
  Acceptance criteria (agent-executable):
    1. **First run, full range**: `uv run super-quality run --start 2015-01-01 --end 2015-12-31`
       - `data/raw/price_2015.parquet` created
       - `data/raw/price_meta.json` exists with `{"years": {"2015": {"req_start": "2015-01-01", "req_end": "2015-12-31"}}}`
    2. **Incremental, extend forward**: `uv run super-quality run --start 2015-01-01 --end 2016-06-30`
       - Only 2016 is downloaded (log shows no 2015 re-download)
       - `data/raw/price_2016.parquet` created
       - `price_meta.json` updated with 2016 entry
    3. **Partial-year extend backward**: 
       - `uv run super-quality run --start 2015-07-01 --end 2015-12-31` (fresh cache)
       - `uv run super-quality run --start 2015-01-01 --end 2015-12-31`
       - Log shows "재다운로드 (부분 캐시)" for 2015
       - Output has full Jan-Dec 2015 data
    4. **Re-run same dates**: second identical run shows "캐시 적중" log, zero downloads
    5. `meta.json` deleted → fresh full re-download (treated as first run)
  QA scenarios:
    - Happy: first-run-split → incremental → partial-year reload → re-run-cache-hit
    - Edge: empty year (no trading days) → warning logged, no crash
    - Edge: `price_meta.json` deleted → next run treats as first run, full re-download
    - Edge: `start == end` (single day) → correct filter, no crash
    - Edge: KOSDAQ ticker list empty → empty DataFrame returned, no crash
    Evidence: `.omo/evidence/task-3-incremental-price-cache/` — run logs, parquet listing, meta.json snapshots (before/after each step)
  Commit: Y | `feat(loader): yearly incremental cache for get_price_data`

- [x] 4. Smoke test & verification
  What to do / Must NOT do:
    - Phase A — Basic incremental (forward extension):
      1. Fresh start: `rm -rf data/raw/price_*`
      2. `uv run super-quality run --start 2015-01-01 --end 2015-06-30` (short range)
      3. Verify: `data/raw/price_2015.parquet` exists, `price_meta.json` has `"2015"` key with `req_start`/`req_end`
      4. Re-run identical dates: verify log shows `"캐시 적중"`, zero downloads, output same
      5. `uv run super-quality run --start 2015-01-01 --end 2016-06-30` (extend forward)
      6. Verify: `price_2016.parquet` now exists, meta has both `"2015"` and `"2016"`, log shows download only for 2016
    - Phase B — Partial-year backward extension (Momus blocker scenario):
      1. Fresh start: `rm -rf data/raw/price_*`
      2. `uv run super-quality run --start 2015-07-01 --end 2015-12-31` (only H2)
      3. Verify: `price_2015.parquet` has only Jul-Dec data (check meta `req_start == "2015-07-01"`)
      4. `uv run super-quality run --start 2015-01-01 --end 2015-12-31` (extend backward to Jan)
      5. Verify: log shows `"재다운로드 (부분 캐시)"` for 2015, `price_meta.json` updated with Jan data
      6. Verify: backtest output has full year data (not missing Jan-May)
    - Must NOT leave test cache files outside `data/raw/`
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: —
  References: `src/super_quality/main.py:82-146` (full pipeline)
  Acceptance criteria (agent-executable): All smoke test steps in Phase A and Phase B pass; pipeline completes without error.
  QA scenarios: N/A (this IS the QA)
  Commit: N

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — verify all Must have items delivered, no Must NOT have items violated
- [x] F2. Code quality review — ruff, types, no dead code
- [x] F3. Real manual QA — run the two-phase smoke test, verify file creation and correct output
- [x] F4. Scope fidelity — no unintended changes to other loader functions, main.py, or backtest engine

## Commit strategy
1. `feat(cache): add put_json/get_json for lightweight metadata`
2. `refactor(loader): extract _download_ticker_batch helper from get_price_data`
3. `feat(loader): yearly incremental cache for get_price_data`

## Success criteria
- Two backtest runs with different end dates: second run downloads only the new year's data
- All existing tests pass (run `uv run pytest -x -q`)
- `price_meta.json` and `price_YYYY.parquet` files correctly reflect cached state
- Backtest output (`tearsheet.html` etc.) is identical before/after refactoring
