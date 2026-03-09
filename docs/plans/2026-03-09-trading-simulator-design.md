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

A `TradingSimulator` lives in the merged feed-predict worker as another sink on `FeedDataService`. It receives two inputs:

- **Feed ticks** — to mark-to-market all open positions
- **Signals from models** — to open, close, or adjust positions

The score worker stays stateless and periodic — it reads portfolio snapshots the simulator writes and computes leaderboard metrics.

### Data Flow

```
Feed tick arrives (FeedDataService)
  → RepositorySink        — persist feed record to DB
  → PredictSink           — feed models, collect signals, save predictions
  → SimulatorSink.on_tick(subject, price, timestamp)
      → mark-to-market all open positions for that subject
      → apply carry costs if interval elapsed
      → write PortfolioSnapshot per model

When a model produces a signal:
  PredictSink saves PredictionRecord
  → SimulatorSink.on_signal(model_id, subject, signal, price, timestamp)
      → open/close/adjust position
      → record Trade (immutable log)
      → apply trading fees + spread

Score worker (polls on interval, unchanged):
  → reads PortfolioSnapshots
  → computes metrics: Sharpe, max drawdown, total return, win rate on closed trades
  → writes SnapshotRecord → leaderboard → checkpoint
```

### State Model

**Position** (mutable, one per model per subject):

| Field | Type | Description |
|-------|------|-------------|
| model_id | str | Which model holds this position |
| subject | str | Trading pair (e.g. "BTCUSDT") |
| direction | str | "long" or "short" |
| size | float | Position size from signal magnitude (0.0–1.0) |
| entry_price | float | Price when position was opened |
| opened_at | datetime | When position was opened |
| current_price | float | Latest mark-to-market price |
| unrealized_pnl | float | Current unrealized P&L |
| accrued_carry | float | Accumulated carry costs |

**Trade** (immutable log, one per position open/close):

| Field | Type | Description |
|-------|------|-------------|
| model_id | str | Which model made this trade |
| subject | str | Trading pair |
| direction | str | "long" or "short" |
| entry_price | float | Price at entry |
| exit_price | float | Price at exit (null if still open) |
| opened_at | datetime | When opened |
| closed_at | datetime | When closed (null if still open) |
| realized_pnl | float | P&L after close (null if still open) |
| fees_paid | float | Trading fees + spread on entry/exit |

**PortfolioSnapshot** (written on each tick or at configurable intervals):

| Field | Type | Description |
|-------|------|-------------|
| model_id | str | Which model's portfolio |
| timestamp | datetime | Snapshot time |
| total_realized_pnl | float | Sum of all closed trade P&L |
| total_unrealized_pnl | float | Sum of all open position P&L |
| total_fees | float | All trading fees paid |
| total_carry_costs | float | All carry costs accrued |
| net_pnl | float | realized + unrealized - fees - carry |
| open_position_count | int | Number of open positions |
| peak_value | float | Highest net_pnl seen (for drawdown) |
| drawdown | float | Current drawdown from peak |

### Cost Model

Three configurable cost layers, set by the competition operator:

1. **Trading fees** — applied on position open and close (e.g. 1-10 bps per trade)
2. **Spread** — applied on entry and exit (e.g. 1 bps)
3. **Carry cost** — accrues continuously while a position is open (e.g. annual rate / 365 / ticks-per-day). Covers funding rates (crypto perps), borrow costs (short selling), margin interest.

All costs are deducted from P&L. Models never see these costs — they emit signals, the simulator applies costs when computing P&L.

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

Order mode is the default — it maps directly to how traders think and matches industry-standard prop trading evaluation platforms. Target mode is a convenience for simpler competitions where models just output a directional conviction.

### Integration with Existing Pipeline

The `TradingSimulator` is a new sink on `FeedDataService`, alongside `RepositorySink` and `PredictSink`. It plugs into the existing merged feed-predict worker.

The score worker reads `PortfolioSnapshot` instead of resolving ground truth from feed records. For trading competitions:
- `resolve_horizon_seconds` is not used (positions are scored continuously)
- The scoring function computes metrics from portfolio snapshot history
- Aggregation windows (24h, 72h, 168h) apply to portfolio P&L curves, not individual prediction scores

### What Stays the Same

- Feed ingestion (FeedDataService, providers, normalizers)
- Model interface (feed_update + predict via gRPC)
- PredictionRecord storage (signals are still saved as predictions)
- Leaderboard ranking and display
- Checkpoint / emission pipeline
- Merkle tamper evidence
- Report API / UI

### What Changes

- New `SimulatorSink` class (implements the same sink interface as PredictSink)
- New DB tables: positions, trades, portfolio_snapshots
- Trading pack's `CrunchConfig` wires the simulator sink instead of using horizon-based scoring
- Score worker gets an alternative scoring path that reads portfolio snapshots
- New cost model configuration on `CrunchConfig`
