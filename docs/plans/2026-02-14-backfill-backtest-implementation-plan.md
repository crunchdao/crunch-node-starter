# Backfill & Backtest — Implementation Plan

Reference: [Design Doc](./2026-02-14-backfill-backtest-design.md)

## Phase 1: Backfill Job Persistence

### Task 1.1: BackfillJobRow table
**File:** `coordinator_node/db/tables/backfill.py` (new)
- SQLModel table `backfill_jobs` with columns: id, source, subject, kind, granularity, start_ts, end_ts, cursor_ts, records_written, pages_fetched, status, error, created_at, updated_at
- Status enum: pending, running, completed, failed

**File:** `coordinator_node/db/tables/__init__.py` (modify)
- Export `BackfillJobRow`

**File:** `coordinator_node/db/init_db.py` (modify)
- Add `backfill_jobs` to `tables_to_reset()`

### Task 1.2: DBBackfillJobRepository
**File:** `coordinator_node/db/backfill_jobs.py` (new)
- `create(source, subject, kind, granularity, start_ts, end_ts) → BackfillJob`
- `get(job_id) → BackfillJob | None`
- `find(status=None) → list[BackfillJob]`
- `update_progress(job_id, cursor_ts, records_written, pages_fetched)`
- `set_status(job_id, status, error=None)`
- `get_running() → BackfillJob | None` (for single-job enforcement)

**File:** `coordinator_node/db/__init__.py` (modify)
- Export `DBBackfillJobRepository`

### Task 1.3: Tests for backfill job persistence
**File:** `tests/test_backfill_jobs.py` (new)
- Test create, find, update_progress, set_status, get_running
- Use SQLite in-memory (same pattern as other repo tests)

---

## Phase 2: Parquet Sink

### Task 2.1: ParquetBackfillSink
**File:** `coordinator_node/services/parquet_sink.py` (new)
- `ParquetBackfillSink(base_dir="data/backfill")`
- `append_records(records: list[FeedRecord]) → int` — same interface as DBFeedRecordRepository
  - Groups records by date
  - For each date: reads existing parquet if present, merges, deduplicates by ts_event, writes back
  - Flattens `values` dict → typed columns (open, high, low, close, volume)
  - Non-standard keys go into `meta` JSON column
- `set_watermark(state)` — no-op (backfill jobs table tracks progress instead)
- Hive path: `{base_dir}/{source}/{subject}/{kind}/{granularity}/YYYY-MM-DD.parquet`

**Dependencies:** Add `pyarrow` to pyproject.toml

### Task 2.2: Tests for parquet sink
**File:** `tests/test_parquet_sink.py` (new)
- Test write, read-back, deduplication on overlap, correct partitioning
- Test flattening of values dict
- Use tmp_path fixture

---

## Phase 3: Modified BackfillService

### Task 3.1: Backfill service with job tracking + parquet sink
**File:** `coordinator_node/services/backfill.py` (modify)
- Add `job_repository: DBBackfillJobRepository | None = None` param
- When job_repository is set: update progress after each page, set status on completion/failure
- Support resume: if `BackfillRequest` includes `cursor_ts`, start from there
- Accept any repository with `append_records` interface (ParquetBackfillSink or DB repo)

### Task 3.2: Update existing backfill tests
**File:** `tests/test_backfill.py` (modify)
- Add test for job progress tracking
- Add test for resume from cursor

---

## Phase 4: API Endpoints

### Task 4.1: Backfill management endpoints
**File:** `coordinator_node/workers/report_worker.py` (modify)
- `GET /reports/backfill/feeds` — reuses `list_indexed_feeds()`
- `POST /reports/backfill` — validates feed exists, checks no running job, creates job, starts async backfill via `BackgroundTasks`. Returns job record or 409.
- `GET /reports/backfill/jobs` — lists all jobs
- `GET /reports/backfill/jobs/{job_id}` — single job with progress percentage

### Task 4.2: Data serving endpoints
**File:** `coordinator_node/workers/report_worker.py` (modify)
- `GET /data/backfill/index` — scans `data/backfill/` directory, returns manifest (path, records count from parquet metadata, file size, date)
- `GET /data/backfill/{source}/{subject}/{kind}/{granularity}/{filename}` — serves parquet file via `FileResponse`

### Task 4.3: Tests for new endpoints
**File:** `tests/test_backfill_endpoints.py` (new)
- Test POST creates job and returns it
- Test POST returns 409 when job already running
- Test GET jobs list
- Test GET index returns manifest
- Test GET file serves parquet
- Use TestClient + tmp directories for parquet files

---

## Phase 5: Docker / Volume Setup

### Task 5.1: Docker compose updates
**File:** `docker-compose.yml` (modify)
- Add `data/backfill` volume mount to report-worker
- Add `BACKFILL_DATA_DIR` env var (default: `/app/data/backfill`)

**File:** `.gitignore` (modify)
- Add `data/backfill/`

---

## Phase 6: Challenge Package Backtest

### Task 6.1: BacktestClient
**File:** `scaffold/challenge/starter_challenge/backtest.py` (new)
- `BacktestClient(coordinator_url: str, cache_dir: str = ".cache/backtest")`
- `pull(source, subject, kind, granularity, start, end, refresh=False)` — fetches index, downloads matching parquet files to cache dir, skips existing unless refresh
- `list_cached()` — returns list of cached file paths
- Uses only `requests` (or `urllib`) — no coordinator-node dependency

### Task 6.2: BacktestRunner
**File:** `scaffold/challenge/starter_challenge/backtest.py` (same file)
- `BacktestRunner(model: TrackerBase, scoring_fn=None, cache_dir=".cache/backtest")`
- `run(source, subject, kind, granularity, start, end, window_size=120, prediction_interval_seconds=60, horizon_seconds=60) → BacktestResult`
- Reads cached parquet with pandas
- Replays chronologically: builds rolling window, calls `model.tick(data)`, calls `model.predict()` at intervals
- Scores each prediction against actual future data using `scoring_fn` (defaults to challenge's `score_prediction`)
- Computes rolling window metrics (score_recent=24h, score_steady=72h, score_anchor=168h)

### Task 6.3: BacktestResult
**File:** `scaffold/challenge/starter_challenge/backtest.py` (same file)
- `predictions_df` — pandas DataFrame: ts, output, actual, score, cumulative metrics
- `metrics` — dict of rolling window aggregates
- `summary()` — prints formatted table
- `_repr_html_()` — for Jupyter notebook rendering

### Task 6.4: Update challenge package dependencies
**File:** `scaffold/challenge/pyproject.toml` (modify)
- Add `pandas`, `pyarrow`, `requests` to dependencies

### Task 6.5: Export from package
**File:** `scaffold/challenge/starter_challenge/__init__.py` (modify)
- Add `BacktestClient`, `BacktestRunner`, `BacktestResult` to exports

### Task 6.6: Tests for backtest
**File:** `tests/test_backtest_harness.py` (new)
- Test BacktestClient caching (mock HTTP, verify files written, verify skip on second call)
- Test BacktestRunner replay loop (synthetic parquet data, dummy model, verify tick/predict call count and order)
- Test BacktestResult metrics computation
- Test scoring matches production scoring function

---

## Implementation Order

1. **Phase 1** (Task 1.1–1.3) — backfill job table + repo
2. **Phase 2** (Task 2.1–2.2) — parquet sink
3. **Phase 3** (Task 3.1–3.2) — wire backfill service to parquet + jobs
4. **Phase 4** (Task 4.1–4.3) — API endpoints
5. **Phase 5** (Task 5.1) — docker/volume
6. **Phase 6** (Task 6.1–6.6) — challenge package backtest harness

Each phase is independently testable. Phases 1–4 are coordinator-side. Phase 6 is challenge-package-side with no coordinator dependency at runtime.
