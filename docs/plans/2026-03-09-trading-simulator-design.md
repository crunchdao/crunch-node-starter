# Trading Simulator Design

**Status**: Draft

## Problem

The current scoring pipeline assumes stateless, horizon-based predictions: a model predicts at t=0, ground truth resolves at t=horizon, and the prediction is scored once. This works for forecasting competitions ("predict the price in 60 seconds") but not for trading signal competitions where:

- A signal results in a position that stays open until closed or reversed
- P&L evolves continuously while the position is held
- Portfolio-level metrics (drawdown, Sharpe) depend on the full P&L curve, not individual scores
- Costs accrue over time (carry costs, funding rates)

The current trading pack works around this by chopping continuous trading into independent 60-second bets, which ranks models by signal quality but doesn't simulate actual trading.

## Design

### Overview

A `TradingSimulator` receives two inputs:

- **Feed ticks** — via feed sink interface (`on_record`) to mark-to-market all open positions
- **Orders from models** — via `post_predict_hook` on `RealtimePredictService`

The simulator writes portfolio snapshots into the existing `SnapshotRecord` table. The score worker reads these snapshots to compute leaderboard metrics — unchanged from today.

### Data Flow

```
Feed tick arrives (FeedDataService)
  → RepositorySink              — persist feed record to DB (log)
  → PredictSink                 — feed models, collect signals, save predictions (log)
  → SimulatorSink.on_record()   — extract price, mark-to-market all open positions

When a model produces a signal:
  RealtimePredictService.process_tick()
    → calls model.predict() → gets signal
    → saves PredictionRecord (log)
    → post_predict_hook → SimulatorSink.on_signal(model_id, subject, signal, price)
        → open/close/adjust position
        → apply trading fees + spread

SimulatorSink writes SnapshotRecord per model (on tick or configurable interval):
  → result_summary = {net_pnl, realized_pnl, unrealized_pnl, fees, carry, drawdown, ...}

Score worker (polls on interval, unchanged):
  → reads SnapshotRecords
  → windowed aggregation (24h, 72h, 168h) on value_field="net_pnl"
  → rebuilds leaderboard → checkpoint → emission
```

### Hookup

Two existing extension points, no new plumbing:

1. **Ticks**: `SimulatorSink` is added as a sink on `FeedDataService` in `predict_worker.py`, alongside `RepositorySink` and `PredictSink`. Extracts price from `record.values` (`close` or `price` field — same logic as existing normalizers).

2. **Orders**: wired via `RealtimeServiceConfig.post_predict_hook` in `CrunchConfig`. The predict service calls the simulator after models respond. No changes to `PredictSink` or `process_tick()`.

### What Becomes Logs

In the simulator setup, these repositories are write-only (audit trail + backtesting):

- **InputRepository** — the simulator has the price from the tick
- **PredictionRepository** — the simulator receives signals via hook, not DB query
- **FeedRecordRepository** — the simulator gets prices from ticks, not by querying feed_records
- **ScoreRepository** — no per-prediction scores; P&L is portfolio-level

### State Model

**Position** (mutable, one per model per subject, in-memory):

| Field | Type | Description |
|-------|------|-------------|
| model_id | str | Which model holds this position |
| subject | str | Trading pair (e.g. "BTCUSDT") |
| direction | str | "long" or "short" |
| leverage | float | Position leverage (e.g. 0.5x) |
| entry_price | float | Price when position was opened |
| opened_at | datetime | When position was opened |
| current_price | float | Latest mark-to-market price |
| unrealized_pnl | float | Current unrealized P&L |
| accrued_carry | float | Accumulated carry costs |

**Trade** (immutable log):

| Field | Type | Description |
|-------|------|-------------|
| model_id | str | Which model made this trade |
| subject | str | Trading pair |
| direction | str | "long" or "short" |
| leverage | float | Order leverage |
| entry_price | float | Price at entry |
| exit_price | float | Price at exit (null if still open) |
| opened_at | datetime | When opened |
| closed_at | datetime | When closed (null if still open) |
| realized_pnl | float | P&L after close (null if still open) |
| fees_paid | float | Trading fees + spread on entry/exit |

**Output**: `SnapshotRecord` (existing table, written by simulator):

| result_summary field | Type | Description |
|---------------------|------|-------------|
| net_pnl | float | realized + unrealized - fees - carry |
| realized_pnl | float | Sum of all closed trade P&L |
| unrealized_pnl | float | Sum of all open position P&L |
| total_fees | float | All trading fees paid |
| total_carry_costs | float | All carry costs accrued |
| open_position_count | int | Number of open positions |
| peak_value | float | Highest net_pnl seen (for drawdown) |
| drawdown | float | Current drawdown from peak |

### Cost Model

Three configurable cost layers, expressed as percentages that scale with leverage:

1. **Trading fees** — on position open and close, scaled by leverage (e.g. 0.1% × leverage)
2. **Spread** — on entry and exit, scaled by leverage
3. **Carry cost** — annual rate accrued while position is held, scaled by leverage (e.g. 10.95% / year for crypto at 1x). Covers funding rates, borrow costs, margin interest.

### Signal Interpretation

The simulator supports two signal modes, configurable per competition:

**Order mode (delta)** — each signal is an order that adjusts the current position. This is how real trading works (cf. Vanta/PTN on Bittensor):

- `{direction: "long", leverage: 0.5}` → add 0.5x long to current position
- `{direction: "short", leverage: 0.3}` on a 0.5x long → reduces to 0.2x long
- Orders accumulate: LONG 0.3x + LONG 0.2x = 0.5x long position
- Positions are uni-directional — shorting past zero closes the position; a new short position opens with the remainder
- Leverage is bounded per position and per portfolio (configurable limits)

**Target mode** — each signal is a desired position state. The simulator computes the trades needed to reach that target:

- `signal = 0.7` → target 70% long (if currently 30% long, the simulator generates a 40% LONG order)
- `signal = -0.5` → target 50% short
- `signal = 0.0` → close all positions for this subject

Order mode is the default — it maps directly to how traders think and matches industry-standard prop trading evaluation platforms.

### What Stays the Same

- Feed ingestion (FeedDataService, providers, normalizers)
- Model interface (feed_update + predict via gRPC)
- PredictionRecord storage (as log)
- SnapshotRecord table (simulator writes to it)
- Leaderboard ranking (reads from SnapshotRecord)
- Checkpoint / emission pipeline
- Merkle tamper evidence
- Report API / UI

### What Changes

- New `TradingSimulator` class with position tracking, mark-to-market, cost model
- New `SimulatorSink` (feed sink + post_predict_hook receiver)
- Trading pack's `CrunchConfig` wires the simulator via `post_predict_hook` and adds `SimulatorSink` to feed sinks
- Score worker skips prediction-based scoring when simulator snapshots are present
- New cost model and leverage config on `CrunchConfig`
