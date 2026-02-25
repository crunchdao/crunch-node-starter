Build: Vanta Coordinator — Order-Based Crypto Trading Competition

 Build a Crunch coordinator (https://github.com/crunchdao/coordinator-node-starter) that replicates Vanta Network (https://github.com/taoshidev/vanta-network/blob/main/docs/miner.md) miner game dynamics as a competition. Models act as traders — submitting leveraged
 LONG/SHORT/FLAT orders on live Binance crypto data. The coordinator tracks positions, applies fees, scores PnL, enforces lifecycle rules, and ranks models on a leaderboard.

 Use the crunch-coordinate skill to scaffold the coordinator workspace, then customize it with the spec below.

 ────────────────────────────────────────────────────────────────────────────────

 Framework Context — coordinator-node-starter conventions

 The coordinator-node-starter framework has specific conventions you MUST follow:

 ### Config
 - **Single source of truth**: `node/config/crunch_config.py` defines a `CrunchConfig` subclass
 - **No separate JSON files** for prediction schedules — use `CrunchConfig.scheduled_predictions`
 - **No `contracts.py`** or `contract_loader.py` — removed. Config loaded via `config_loader.load_config()`
 - Config path: `config/crunch_config.py` (NOT `runtime_definitions/`)

 ### Type-safe JSONB
 CrunchConfig declares 5 Pydantic types that define the data shape at every JSONB boundary:
 - `raw_input_type` — what the feed produces (validated on ingest)
 - `input_type` — what models receive (can differ from raw_input_type)
 - `output_type` — what models return (validated before DB write)
 - `ground_truth_type` — what actuals look like (validated on resolution)
 - `score_type` — what scoring produces (validated before DB write)

 Services use these types directly: `config.output_type.model_validate(data)` to parse,
 `validated.model_dump()` to serialize. No wrapper methods — the Pydantic models ARE the
 parse/dump interface.

 ### Input is a dumb log
 - `InputRecord` has only: `id`, `raw_data`, `received_at` — NO status, actuals, scope, resolvable_at
 - `InputRow` (DB) has only: `id`, `received_at`, `raw_data_jsonb`
 - Input is saved once and never updated

 ### Predictions own their resolution
 - `PredictionRecord` carries: `scope` (with feed dimensions), `resolvable_at`, `performed_at`
 - Score worker queries predictions by `status=PENDING, resolvable_before=now`
 - Ground truth is resolved per-prediction using the prediction's own scope and time window
 - For `resolve_horizon_seconds=0`: immediate resolution — score worker looks up the
   prediction's `InputRecord.raw_data` via `input_repository.get()` and passes it as
   ground truth. No feed query. Raises `RuntimeError` if `input_repository` is None
   (misconfiguration). Skips with warning if the input record is missing.

 ### Aggregation
 - `Aggregation.value_field` (default `"value"`) — the score field name to extract from
   each snapshot's `result_summary` for windowed averaging. Must match a numeric key in
   your ScoreResult type.
 - `Aggregation.ranking_key` — which key in the final metrics dict to rank by. Can be a
   window name (e.g. `"pnl_24h"`) or a score field name (e.g. `"net_pnl"`).
 - Windows average `value_field` over their time range. Latest snapshot's numeric fields
   are also merged into the leaderboard metrics, so custom score fields appear automatically.

 ### Scoring → Snapshots → Leaderboard data flow
 1. `scoring_function(prediction, ground_truth)` → returns dict matching `score_type`
 2. `score_type.model_validate(result)` → `ScoreRecord.result` (JSONB)
 3. `aggregate_snapshot([results...])` → averages all numeric fields → `SnapshotRecord.result_summary`
 4. `_aggregate_from_snapshots()` → reads `value_field` from each snapshot for windows,
    merges latest snapshot's numeric fields → leaderboard `metrics` dict
 5. `auto_report_schema()` → introspects `score_type.model_fields` → auto-generates
    leaderboard columns for all numeric fields not already covered by windows

 ### Naming
 - `resolve_horizon_seconds` (not `resolve_after_seconds` or `horizon_seconds`)
 - `prediction_interval_seconds` (not `step_seconds` for scheduling)
 - `step_seconds` is the feed granularity hint passed to models

 ### No transform callable
 - `PredictService` has no `transform` parameter — removed as dead code
 - Raw feed data is validated through `raw_input_type.model_validate()` directly

 ────────────────────────────────────────────────────────────────────────────────

 Design Principles

 1. Minimal core, extensible later. Only build what's specified. No backtesting harness, no miner layer, no ensemble logic.
 2. Everything typed. Dataclasses for domain objects. Pydantic for framework contracts. Type hints on every function signature. All JSONB boundaries validated through CrunchConfig types.
 3. Stateful position tracking. The scoring function is a thin passthrough. All PnL computation happens in the PositionManager extension, which maintains per-model portfolio state across prediction cycles.
 4. Models can do nothing. trade() returns Order | None. None means no action this cycle — the model still gets a portfolio snapshot (mark-to-market update) but no order is executed.
 5. All constants from env vars. Every Vanta parameter (leverage limits, fee rates, lifecycle thresholds) is read from environment variables with sensible defaults. Nothing hardcoded in logic.
 6. Tests for every extension. Position rules, fee math, lifecycle transitions — all covered.

 ────────────────────────────────────────────────────────────────────────────────

 Architecture

 ```
   challenge/                          # pip-installable participant package
     vanta/
       __init__.py
       types.py                        # Order, Position, Candle, MarketData dataclasses
       tracker.py                      # TrackerBase with trade() interface
       scoring.py                      # score_prediction() passthrough
       config.py                       # package metadata

   node/                               # coordinator infrastructure
     config/
       crunch_config.py                # CrunchConfig subclass (types, predictions, callables)
     extensions/
       __init__.py
       position_manager.py             # Order → Position → Portfolio → Snapshot
       fee_engine.py                   # Carry (scheduled), spread, slippage
       lifecycle_manager.py            # CHALLENGE → MAINCOMP → PROBATION → ELIMINATED
     api/
       __init__.py
       positions.py                    # Custom REST endpoints for position/lifecycle state
     deployment/
       model-orchestrator-local/
         config/
           starter-submission/         # example model
           models.dev.yml
         data/submissions/
           starter-submission/         # deployed copy
     scripts/
       verify_e2e.py
     docker-compose.yml
     Dockerfile
     Makefile
     .local.env

   tests/                              # pytest suite (NOT inside node/ or challenge/)
     __init__.py
     test_position_manager.py
     test_fee_engine.py
     test_lifecycle_manager.py
     test_scoring.py
     test_tracker.py

   Makefile                            # top-level: deploy, test, logs
   README.md
 ```

 ────────────────────────────────────────────────────────────────────────────────

 1. Model Interface — challenge/vanta/tracker.py

 ```python
   class TrackerBase:
       def tick(self, data: dict) -> None: ...
       def trade(self) -> Order | None: ...
       def predict(self, **kwargs) -> dict | None: ...  # framework adapter, do NOT override
 ```

 - tick(data) receives per-symbol market data (1m/5m/15m/1h candles, optional orderbook + funding). Called every feed update. Store data keyed by data["symbol"].
 - trade() is the method participants implement. Returns Order("LONG", "BTCUSDT", leverage=0.5) or None. Has access to self.positions (dict of current open positions, updated by coordinator) and self._latest_data / self._history.
 - predict(**kwargs) is the framework adapter. Absorbs the framework's call args (subject, resolve_horizon_seconds, step_seconds), calls trade(), converts Order to dict, passes None through unchanged. Participants never override this.

 ────────────────────────────────────────────────────────────────────────────────

 2. Typed Contracts — challenge/vanta/types.py

 All domain types as dataclasses with validation in __post_init__:

 ```python
   SUPPORTED_PAIRS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"]
   VALID_ACTIONS: set[str] = {"LONG", "SHORT", "FLAT"}

   @dataclass
   class Order:
       action: str           # LONG, SHORT, FLAT
       trade_pair: str       # must be in SUPPORTED_PAIRS
       leverage: float = 0.0 # 0.001–2.5 for crypto; ignored for FLAT
       # __post_init__ validates action, trade_pair, leverage range

   @dataclass
   class Position:
       direction: str        # LONG, SHORT
       leverage: float
       entry_price: float
       unrealized_pnl: float = 0.0
       fees_accrued: float = 0.0

   @dataclass
   class Candle:
       open: float
       high: float
       low: float
       close: float
       volume: float
       timestamp: int = 0

   @dataclass
   class MarketData:
       symbol: str = "BTCUSDT"
       asof_ts: int = 0
       candles_1m: list[dict] = field(default_factory=list)
       candles_5m: list[dict] = field(default_factory=list)
       candles_15m: list[dict] = field(default_factory=list)
       candles_1h: list[dict] = field(default_factory=list)
       orderbook: dict | None = None
       funding: dict | None = None
 ```

 ────────────────────────────────────────────────────────────────────────────────

 3. Position Manager — node/extensions/position_manager.py

 This is the core engine. Maintains a PortfolioState per model with open_positions and closed_positions.

 ### Data structures (all dataclasses, all typed):

 - Order — action, trade_pair, leverage, timestamp, price, spread_fee, slippage_fee, accepted, rejected_reason
 - Position — trade_pair, direction, leverage, entry_price, entry_ts, max_seen_leverage, fees (carry/spread/slippage), realized_pnl, is_open, close_ts/price. Methods: unrealized_pnl(price), net_pnl(price), total_fees.
 - PortfolioState — model_id, open_positions dict, closed_positions list, peak_value, current_value, last_order_ts dict, registration_ts.

 ### PositionManager class:

 Constructor takes: supported_pairs, position_leverage_min, position_leverage_max, portfolio_leverage_max, order_cooldown_seconds, spread_fee_rate, slippage_bps. All from env vars.

 process_order(model_id, order_dict, current_prices, ts) → dict:
 1. If order_dict is None → skip to snapshot (no order executed, model chose inaction)
 2. Validate: action ∈ {LONG, SHORT, FLAT}, pair is supported, cooldown not active (10s per pair), leverage ≥ min
 3. Execute:
     - FLAT → close position, book realized PnL
     - New position → create with clamped leverage (per-position max, portfolio max)
     - Same direction → increase leverage, weighted-average entry price
     - Opposite direction, partial → reduce leverage
     - Opposite direction, full+ → close existing (book PnL), open remainder in opposite direction
 4. Apply spread fee (spread_rate × order_leverage) and slippage fee (slippage_bps/10000 × order_leverage) at order time
 5. Compute and return snapshot dict: portfolio_value, unrealized_pnl, realized_pnl, total_fees, gross_pnl, net_pnl, drawdown_pct, portfolio_leverage, open_positions count, order_accepted, order_rejected_reason

 get_positions_for_model(model_id) → dict[str, dict]: simplified position view for passing to model's self.positions.

 ### Position rules (Vanta parity):

 - Uni-directional: positions can't flip. Excess opposite leverage closes + reopens.
 - Max 1 open position per pair per model.
 - 10-second cooldown between orders on same pair.
 - Leverage below minimum → rejected (not clamped). Above maximum → clamped (not rejected).
 - Portfolio leverage (sum of all open) → clamped (not rejected).

 ────────────────────────────────────────────────────────────────────────────────

 4. Fee Engine — node/extensions/fee_engine.py

 Three fee types:

 ┌──────────┬───────────────┬────────────────────────────────────┬────────────────────────────────────────────────────┐
 │ Fee      │ Rate          │ When                               │ Formula                                            │
 ├──────────┼───────────────┼────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ Spread   │ 0.1%          │ Each order                         │ SPREAD_FEE_RATE × order_leverage                   │
 ├──────────┼───────────────┼────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ Slippage │ 3 bps         │ Each order                         │ (SLIPPAGE_BPS / 10000) × order_leverage            │
 ├──────────┼───────────────┼────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ Carry    │ 10.95% annual │ Every 8h (04:00, 12:00, 20:00 UTC) │ (daily_rate × max_seen_leverage) / periods_per_day │
 └──────────┴───────────────┴────────────────────────────────────┴────────────────────────────────────────────────────┘

 Key: carry uses max leverage ever seen on that position, not current leverage. Tracked per model_id:trade_pair. Applied once per carry hour (deduplicated).

 ### FeeEngine class:

 - compute_spread_fee(leverage) → float
 - compute_slippage_fee(leverage) → float
 - should_apply_carry(model_id, trade_pair, ts) → bool
 - apply_carry_fees(portfolio, ts) → float (total carry applied this cycle)

 All rates configurable via env vars: CARRY_ANNUAL_RATE, CARRY_INTERVAL_HOURS, CARRY_TIMES_UTC, SPREAD_FEE_RATE, SLIPPAGE_BPS.

 ────────────────────────────────────────────────────────────────────────────────

 5. Lifecycle Manager — node/extensions/lifecycle_manager.py

 State machine per model:

 ```
   Register → IMMUNITY (4h) → CHALLENGE (90d) → MAINCOMP → (optional) PROBATION (60d) → ELIMINATED
 ```

 ┌────────────┬─────────────────────────┬───────────┬───────────────────────────────────────────────────────────────────────────┐
 │ State      │ Entry                   │ Duration  │ Exit                                                                      │
 ├────────────┼─────────────────────────┼───────────┼───────────────────────────────────────────────────────────────────────────┤
 │ IMMUNITY   │ Registration            │ 4 hours   │ Auto → CHALLENGE                                                          │
 ├────────────┼─────────────────────────┼───────────┼───────────────────────────────────────────────────────────────────────────┤
 │ CHALLENGE  │ After immunity          │ 90 days   │ Pass: 61+ trading days, DD < 10%, rank ≤ 25 → MAINCOMP. Fail → ELIMINATED │
 ├────────────┼─────────────────────────┼───────────┼───────────────────────────────────────────────────────────────────────────┤
 │ MAINCOMP   │ Pass challenge          │ Ongoing   │ DD > 10% → ELIMINATED. Rank drops below 25 → PROBATION                    │
 ├────────────┼─────────────────────────┼───────────┼───────────────────────────────────────────────────────────────────────────┤
 │ PROBATION  │ Demoted from MAINCOMP   │ 60 days   │ Recover to top 25 → MAINCOMP. Timeout → ELIMINATED                        │
 ├────────────┼─────────────────────────┼───────────┼───────────────────────────────────────────────────────────────────────────┤
 │ ELIMINATED │ Any elimination trigger │ Permanent │ Blacklisted, cannot re-register                                           │
 └────────────┴─────────────────────────┴───────────┴───────────────────────────────────────────────────────────────────────────┘

 Probation/rank rules only activate when MIN_MODELS_FOR_PROBATION (default 10) models are competing.

 ### LifecycleManager class:

 - register_model(model_id, ts) → ModelLifecycle
 - update(model_id, drawdown_pct, rank, trading_days, total_models, ts) → ModelLifecycle
 - is_in_immunity(model_id, ts) → bool
 - get_active_models() → list[ModelLifecycle]
 - get_summary() → dict

 ### ModelLifecycle dataclass:

 - model_id, state (enum), registration_ts, challenge_start_ts, maincomp_start_ts, probation_start_ts, elimination_ts, elimination_reason, trading_days, current_rank, max_drawdown_pct

 All thresholds from env vars: CHALLENGE_PERIOD_DAYS, CHALLENGE_MIN_TRADING_DAYS, MAX_DRAWDOWN_PCT, PROBATION_DAYS, PROBATION_RANK_CUTOFF, MIN_MODELS_FOR_PROBATION, IMMUNITY_HOURS.

 ────────────────────────────────────────────────────────────────────────────────

 6. Scoring & Snapshots

 ### Stateful scoring function

 The scoring function is a **stateful callable** — a method on a class that wraps the
 PositionManager. It's wired into `ScoreService` as the `scoring_function` parameter.

 The score worker calls `scoring_function(prediction, ground_truth)` where:
 - `prediction` is the model's order dict (`VantaOutput` shape + injected `model_id`)
 - `ground_truth` is the latest feed data (current prices from `InputRecord.raw_data`
   for `resolve_horizon_seconds=0`)

 The scoring function does ALL the work:
 1. Extracts the order from `prediction` (action, trade_pair, leverage)
 2. Extracts current prices from `ground_truth`
 3. Calls `position_manager.process_order(model_id, order, prices, ts)`
 4. PositionManager updates the trading book: validates order, executes trade,
    applies fees (spread + slippage), marks all positions to market, computes PnL
 5. Returns a `VantaScore`-shaped dict with the full portfolio snapshot

 ```python
 class VantaScoringFunction:
     """Stateful scoring callable wrapping the PositionManager."""

     def __init__(self, position_manager: PositionManager):
         self.pm = position_manager

     def __call__(self, prediction: dict, ground_truth: dict) -> dict:
         model_id = prediction["model_id"]
         prices = {}
         for candle in ground_truth.get("candles_1m", []):
             prices[ground_truth.get("symbol", "BTCUSDT")] = candle.get("close", 0.0)

         order = {
             "action": prediction.get("action", "FLAT"),
             "trade_pair": prediction.get("trade_pair", "BTCUSDT"),
             "leverage": prediction.get("leverage", 0.0),
         }
         snapshot = self.pm.process_order(model_id, order, prices, datetime.now(UTC))
         return {
             "value": snapshot["net_pnl"],
             **snapshot,
             "success": True,
             "failed_reason": None,
         }
 ```

 This callable is instantiated once at worker startup and passed to `ScoreService`.
 The PositionManager maintains state across all scoring cycles.

 ### VantaScore — what goes into each ScoreRecord

 ```python
 class VantaScore(ScoreResult):
     """Point-in-time portfolio snapshot from PositionManager."""
     value: float = 0.0              # = net_pnl (primary score value)
     net_pnl: float = 0.0            # gross_pnl - total_fees
     open_positions: int = 0          # count of open positions
     total_leverage: float = 0.0      # sum of leverage across open positions
     total_trades: int = 0            # cumulative closed position count
     # internal fields (not displayed in leaderboard)
     order_accepted: bool = False
     order_rejected_reason: str | None = None
     success: bool = True
     failed_reason: str | None = None
 ```

 Only 4 fields shown in the leaderboard: **PnL, Open Positions, Total Leverage, Total Trades**.
 Sharpe ratio deferred — requires rolling window over historical snapshots, not a point-in-time field.

 ### Snapshots — what goes into each SnapshotRecord

 Each score cycle, `aggregate_snapshot([score_results...])` averages all numeric fields
 from that cycle's VantaScore results. For Vanta with `resolve_horizon_seconds=0`, there's
 typically one score result per model per cycle, so the snapshot IS the score.

 `SnapshotRecord.result_summary` contains:
 ```json
 {"value": 0.05, "net_pnl": 0.05, "open_positions": 1.0, "total_leverage": 1.5, "total_trades": 3.0, ...}
 ```

 This is the authoritative data source for the leaderboard and all report endpoints.

 ### Leaderboard — how snapshots become rankings

 `_aggregate_from_snapshots()` processes all snapshots per model:
 1. For each window (pnl_24h, pnl_72h, pnl_7d): average `value_field` (`"net_pnl"`)
    from snapshots within the window's time range
 2. Merge ALL numeric fields from the latest snapshot (so `open_positions`,
    `total_leverage`, `total_trades` appear directly)
 3. Rank by `ranking_key` (`"net_pnl"` — from latest snapshot, not a window)

 `auto_report_schema()` introspects `VantaScore.model_fields` and auto-generates
 leaderboard columns for all numeric fields not already covered by windows.

 ### What the leaderboard shows

 | Column          | Source                        | Type     |
 |-----------------|-------------------------------|----------|
 | Model           | model_id                      | MODEL    |
 | PnL 24h         | window avg of net_pnl (24h)   | VALUE    |
 | PnL 72h         | window avg of net_pnl (72h)   | VALUE    |
 | PnL 7d          | window avg of net_pnl (7d)    | VALUE    |
 | Net Pnl         | latest snapshot                | VALUE    |
 | Open Positions  | latest snapshot                | VALUE    |
 | Total Leverage  | latest snapshot                | VALUE    |
 | Total Trades    | latest snapshot                | VALUE    |

 Ranking: descending by `net_pnl` from latest snapshot.

 ────────────────────────────────────────────────────────────────────────────────

 7. CrunchConfig — node/config/crunch_config.py

 Subclass `CrunchConfig` from `coordinator_node.crunch_config`. Override the 5 Pydantic
 types to define Vanta's data shapes. The framework validates all JSONB boundaries through
 these types automatically (`Type.model_validate()` on read, `.model_dump()` on write).

 ```python
 from pydantic import BaseModel, ConfigDict, Field
 from coordinator_node.crunch_config import (
     CrunchConfig as BaseCrunchConfig,
     RawInput,
     ScoreResult as BaseScoreResult,
     PredictionScope,
     ScheduledPrediction,
     Aggregation,
     AggregationWindow,
 )


 class VantaInput(RawInput):
     """What the feed produces AND what models receive."""
     symbol: str = "BTCUSDT"
     asof_ts: int = 0
     candles_1m: list[dict] = Field(default_factory=list)
     candles_5m: list[dict] = Field(default_factory=list)
     candles_15m: list[dict] = Field(default_factory=list)
     candles_1h: list[dict] = Field(default_factory=list)
     orderbook: dict | None = None
     funding: dict | None = None


 class VantaOutput(BaseModel):
     """What models return — an order (or null fields for no action)."""
     model_config = ConfigDict(extra="allow")
     action: str = "FLAT"          # LONG, SHORT, FLAT
     trade_pair: str = "BTCUSDT"
     leverage: float = 0.0


 class VantaScore(BaseScoreResult):
     """Point-in-time portfolio snapshot from PositionManager."""
     net_pnl: float = 0.0            # gross_pnl - total_fees (= value)
     open_positions: int = 0          # count of open positions
     total_leverage: float = 0.0      # sum of leverage across open positions
     total_trades: int = 0            # cumulative closed position count
     order_accepted: bool = False
     order_rejected_reason: str | None = None


 class CrunchConfig(BaseCrunchConfig):
     # Type-safe JSONB boundaries
     raw_input_type: type[BaseModel] = VantaInput
     input_type: type[BaseModel] = VantaInput       # models get raw feed
     output_type: type[BaseModel] = VantaOutput
     score_type: type[BaseModel] = VantaScore
     # ground_truth_type stays default — for horizon=0, raw_data is used directly

     # Stateful scoring (takes precedence over SCORING_FUNCTION env var)
     scoring_function: Any = Field(default_factory=VantaScoringFunction)

     # Prediction schedules
     scheduled_predictions: list[ScheduledPrediction] = Field(
         default_factory=lambda: [
             ScheduledPrediction(
                 scope_key="crypto-live",
                 scope={"subject": "BTCUSDT"},
                 prediction_interval_seconds=60,
                 resolve_horizon_seconds=0,  # immediate — live trading
             ),
         ],
     )

     # Aggregation — windowed PnL + leaderboard ranking
     aggregation: Aggregation = Field(
         default_factory=lambda: Aggregation(
             windows={
                 "pnl_24h": AggregationWindow(hours=24),
                 "pnl_72h": AggregationWindow(hours=72),
                 "pnl_7d": AggregationWindow(hours=168),
             },
             value_field="net_pnl",        # score field to average in windows
             ranking_key="net_pnl",         # rank by latest snapshot's net_pnl
             ranking_direction="desc",
         )
     )

     scope: PredictionScope = Field(
         default_factory=lambda: PredictionScope(subject="BTCUSDT", step_seconds=60)
     )

     # No default metrics (ic, hit_rate, etc.) — not applicable for trading
     metrics: list[str] = []
 ```

 ### How the types flow through the system:

 1. **Feed → Input**: `predict.py` calls `VantaInput.model_validate(raw_feed_dict)` — blows up if feed data is wrong shape. Saved to `inputs.raw_data_jsonb`.
 2. **Model output → Prediction**: `predict.py` calls `VantaOutput.model_validate(output)` — catches models returning wrong schema. Saved to `predictions.inference_output_jsonb`.
 3. **Ground truth → Scoring**: For horizon=0, score worker passes `InputRecord.raw_data` directly as ground truth. No validation through `ground_truth_type` — the data is already validated VantaInput.
 4. **Scoring result → Score**: `score.py` calls `VantaScore.model_validate(result)` — guarantees all portfolio fields present. Saved to `scores.result_jsonb`.
 5. **Score → Snapshot**: `aggregate_snapshot([results])` averages numeric fields → `snapshots.result_summary_jsonb`.
 6. **Snapshot → Leaderboard**: `_aggregate_from_snapshots()` reads `value_field` from snapshots for windows, merges latest numeric fields → `leaderboards.entries_jsonb`.

 ### resolve_ground_truth:

 With `resolve_horizon_seconds=0`, the score worker looks up the prediction's own
 `InputRecord.raw_data` and passes it directly as ground truth. No feed query needed —
 the feed data (prices, candles) was already captured when the prediction was made.

 For `resolve_horizon_seconds > 0` (standard challenges), the score worker uses
 `feed_reader.fetch_window()` → `resolve_ground_truth(records)` as before. The function
 receives `list[FeedRecord]` (dataclass with `.subject`, `.values`, `.ts_event`).

 ### build_emission:

 Tier-based: 1st=35%, 2nd-5th=10% each, 6th-10th=5% each. Unclaimed redistributed equally.

 ────────────────────────────────────────────────────────────────────────────────

 8. API Endpoints — node/api/positions.py

 FastAPI router at /positions, auto-mounted by report worker:

 ┌──────────────────────────────────────┬─────────────────────────────────────────────────────────────┐
 │ Endpoint                             │ Description                                                 │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/rules                 │ Full competition rules (leverage, fees, lifecycle, scoring) │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/pairs                 │ Supported pairs and limits                                  │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/models                │ All models with position summaries                          │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/models/{id}           │ Detailed model view                                         │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/models/{id}/positions │ Open and closed positions                                   │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/models/{id}/orders    │ Paginated order history                                     │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/models/{id}/lifecycle │ Lifecycle state                                             │
 ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
 │ GET /positions/lifecycle/summary     │ Models grouped by state                                     │
 └──────────────────────────────────────┴─────────────────────────────────────────────────────────────┘

 These can start as stub endpoints that return the correct shape. Integration with coordinator DB is a future extension.

 ────────────────────────────────────────────────────────────────────────────────

 9. Example Models

 ### Starter Submission (deployed)

 Simple SMA crossover on BTCUSDT. Goes LONG when fast SMA > slow SMA, SHORT when reversed, FLAT when signal is weak. Returns None when there's insufficient data or the signal is in the dead zone.

 ### 3 example strategies in challenge/vanta/examples/:

 1. Trend Following — dual SMA crossover on BTCUSDT
 2. Mean Reversion — Bollinger Band reversion on ETHUSDT
 3. Volatility Regime — monitors vol across pairs, trades low-vol breakouts

 Each must demonstrate:
 - Returning Order(...) for a trade
 - Returning None for no action
 - Using self.positions to check current state before trading
 - Keying state by symbol for multi-pair support

 ────────────────────────────────────────────────────────────────────────────────

 10. Configuration — node/.local.env

 Every Vanta parameter as a named env var. Group by concern:

 ```env
   # Feed
   FEED_SOURCE=binance
   FEED_SUBJECTS=BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,ADAUSDT
   FEED_KIND=candle
   FEED_GRANULARITY=1m
   FEED_POLL_SECONDS=5
   FEED_BACKFILL_MINUTES=180

   # Leverage
   POSITION_LEVERAGE_MIN=0.001
   POSITION_LEVERAGE_MAX=2.5
   PORTFOLIO_LEVERAGE_MAX=5.0
   ORDER_COOLDOWN_SECONDS=10
   STEP_SECONDS=60

   # Fees
   CARRY_ANNUAL_RATE=0.1095
   CARRY_INTERVAL_HOURS=8
   CARRY_TIMES_UTC=04:00,12:00,20:00
   SPREAD_FEE_RATE=0.001
   SLIPPAGE_BPS=3

   # Lifecycle
   CHALLENGE_PERIOD_DAYS=90
   CHALLENGE_MIN_TRADING_DAYS=61
   MAX_DRAWDOWN_PCT=10.0
   PROBATION_DAYS=60
   PROBATION_RANK_CUTOFF=25
   MIN_MODELS_FOR_PROBATION=10
   IMMUNITY_HOURS=4

   # Scoring
   RISK_FREE_RATE=0.05
 ```

 ────────────────────────────────────────────────────────────────────────────────

 11. Tests — tests/

 ### test_position_manager.py

 - Open long/short, verify direction + leverage + entry price
 - None order → snapshot computed, no position opened
 - Leverage below minimum → rejected
 - Leverage above maximum → clamped (not rejected)
 - Portfolio leverage cap → clamped
 - Same direction → increases leverage, weighted avg entry
 - Opposite direction partial → reduces leverage
 - Opposite direction exceeds → close + reopen remainder
 - FLAT → closes position, books realized PnL
 - FLAT on no position → rejected
 - Cooldown enforced (same pair), not enforced (different pair)
 - Unsupported pair → rejected
 - Invalid action → rejected
 - No price available → rejected
 - Long profit/loss with price movement
 - Short profit/loss with price movement
 - Drawdown tracking (peak → drop)
 - Multi-model independence
 - Fees applied at order time (spread + slippage)

 ### test_fee_engine.py

 - Spread fee = rate × leverage
 - Slippage fee = bps × leverage
 - Carry applied at correct UTC hours (04, 12, 20)
 - Carry NOT applied at other hours
 - Carry uses max_seen_leverage, not current
 - Carry applied once per hour (deduplicated)
 - Carry reset when position closed

 ### test_lifecycle_manager.py

 - New model starts in CHALLENGE
 - Immunity period blocks elimination
 - Drawdown > 10% → ELIMINATED (from any state except immunity)
 - Challenge pass → MAINCOMP (enough days, good rank, low DD)
 - Challenge fail: insufficient trading days → ELIMINATED
 - Challenge fail: bad rank → ELIMINATED
 - MAINCOMP rank drop → PROBATION (only when ≥ MIN_MODELS)
 - PROBATION recovery → MAINCOMP
 - PROBATION timeout → ELIMINATED
 - Eliminated model cannot re-register
 - get_active_models excludes eliminated
 - get_summary groups by state

 ### test_scoring.py

 - score_prediction extracts net_pnl as value
 - Empty snapshot → value = 0.0
 - VantaScore validates with all 4 leaderboard fields

 ### test_tracker.py

 - TrackerBase.trade() raises NotImplementedError
 - Subclass returning Order → predict() returns dict
 - Subclass returning None → predict() returns None
 - tick() stores data per symbol
 - positions dict accessible from trade()
 - Order validation in types.py (__post_init__)

 ────────────────────────────────────────────────────────────────────────────────

 What NOT to Build

 - No miner/live-trading layer (future extension)
 - No indicator library (future extension)
 - No ensemble/multi-model logic (future extension)
 - No custom metrics registry integration (use defaults)
 - No position state persistence to DB (in-memory for now, DB integration is future)
 - No complex report worker customization beyond the API stubs
 - No Sharpe ratio or rolling risk metrics (deferred — requires rolling window over
   historical snapshots, not a point-in-time field. Will add when metrics infrastructure
   is wired for Vanta)
 - No `SCORING_FUNCTION` env var — use `CrunchConfig.scoring_function` (stateful callable)
