# Packs

Packs are overlay directories that customize `scaffold/` for specific competition types.

## How it works

1. CLI copies `scaffold/` to destination
2. If a pack is specified, copies `packs/<pack>/` on top (overwriting matching files)
3. Replaces `starter-challenge` → `<name>` and `starter_challenge` → `<module>` in all files

## Available Packs

| Pack | Output type | Scoring | Schedule | Use case |
|------|-------------|---------|----------|----------|
| **realtime** | `{"value": float}` | prediction × return | 15s interval, 60s horizon | Simplest format — predict next return |
| **trading** | `{"signal": float}` in [-1,1] | PnL with spread | 15s interval, 60s horizon, multi-asset | Trading signal competitions |
| **tournament** | `{"prediction": float}` | IC / residual | 5min interval, 1h horizon | Classic quant tournament |

## What each pack overrides

Packs only contain files that differ from `scaffold/`. Everything else inherits as-is.

### realtime (reference — closest to scaffold defaults)
```
realtime/
├── node/
│   ├── config/crunch_config.py       ← value-based output, single asset
│   └── .local.env.example            ← binance feed, BTCUSDT, 1s candles
└── challenge/
    └── starter_challenge/
        └── scoring.py                ← prediction × return scoring
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
│   ├── .local.env.example            ← binance feed, BTCUSDT, 1m granularity
│   ├── scripts/
│   │   ├── validate_config.py        ← tournament-aware (skips feed/timing checks)
│   │   ├── check_models.py           ← shorter timeout, succeeds if orchestrator up
│   │   └── verify_e2e.py             ← drives rounds via tournament API
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
