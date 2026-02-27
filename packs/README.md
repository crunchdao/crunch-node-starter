# Packs

Packs are overlay directories that customize `scaffold/` for specific competition types.

## How it works

1. CLI copies `scaffold/` to destination
2. If a pack is specified, copies `packs/<pack>/` on top (overwriting matching files)
3. Replaces `starter-challenge` → `<name>` and `starter_challenge` → `<module>` in all files

## Available Packs

| Pack | Output type | Scoring | Schedule | Use case |
|------|-------------|---------|----------|----------|
| **prediction** | `{"value": float}` | Directional accuracy | 15s interval, 60s horizon | Simplest format — predict up/down |
| **trading** | `{"signal": float}` in [-1,1] | PnL with spread | 15s interval, 60s horizon, multi-asset | Trading signal competitions |
| **tournament** | `{"prediction": float}` | IC / residual | 5min interval, 1h horizon | Classic quant tournament |

## What each pack overrides

Packs only contain files that differ from `scaffold/`. Everything else inherits as-is.

### prediction (reference — closest to scaffold defaults)
```
prediction/
├── node/
│   ├── config/crunch_config.py       ← value-based output, single asset
│   └── .local.env.example            ← pyth feed, BTC, 1s ticks
└── challenge/
    └── starter_challenge/
        └── scoring.py                ← direction * magnitude scoring
    └── tests/
        └── test_scoring.py
```

### trading
```
trading/
├── node/
│   ├── config/crunch_config.py       ← signal [-1,1] output, PnL scoring, multi-asset
│   ├── config/callables.env
│   ├── .local.env.example            ← binance feed, BTCUSDT+ETHUSDT, 1m candles
│   └── deployment/report-ui/config/
│       └── leaderboard-columns.json  ← PnL, hit rate, Sortino columns
└── challenge/
    └── starter_challenge/
        ├── scoring.py                ← PnL = signal * return - spread
        ├── tracker.py                ← predict() returns {"signal": float}
        └── examples/
            ├── momentum_tracker.py
            ├── mean_reversion_tracker.py
            └── breakout_tracker.py
    └── tests/
        ├── test_scoring.py
        └── test_examples.py
```

### tournament
```
tournament/
├── node/
│   ├── config/crunch_config.py       ← prediction output, IC ranking, 1h horizon
│   ├── config/callables.env
│   ├── .local.env.example            ← pyth feed, BTC, 1m granularity
│   └── deployment/report-ui/config/
│       └── leaderboard-columns.json  ← IC, IC Sharpe, hit rate columns
└── challenge/
    └── starter_challenge/
        ├── scoring.py                ← negative squared residual
        ├── tracker.py                ← predict() returns {"prediction": float}
        └── examples/
            ├── feature_momentum_tracker.py
            ├── linear_combo_tracker.py
            └── contrarian_tracker.py
    └── tests/
        ├── test_scoring.py
        └── test_examples.py
```

## Creating a new pack

Add a directory here with only the files that differ from `scaffold/`:

1. Start with `node/config/crunch_config.py` — define your types + schedule
2. Add `challenge/starter_challenge/scoring.py` — must match your ScoreResult shape
3. Add `challenge/starter_challenge/tracker.py` if predict() signature changes
4. Add examples and tests
5. Override `.local.env.example` if feed config differs

Files not present in the pack inherit from `scaffold/`.
