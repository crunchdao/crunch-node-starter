# challenge

Public challenge package.

Primary package: `starter_challenge`

## Participant-facing files

- `cruncher.py` — model interface (participants subclass `ModelBaseClass`)
- `scoring.py` — scoring function for local self-eval
- `backtest.py` — backtest harness (`BacktestClient`, `BacktestRunner`, `BacktestResult`)
- `config.py` — baked-in coordinator URL and default feed dimensions
- `examples/` — quickstarter model implementations

## Backtest usage

```python
from starter_challenge.backtest import BacktestRunner
from my_model import MyTracker

result = BacktestRunner(model=MyTracker()).run(
    start="2026-01-01", end="2026-02-01"
)
result.predictions_df   # DataFrame in notebook
result.metrics           # rolling windows + multi-metric enrichment
result.summary()         # formatted output

# result.metrics includes both rolling windows and portfolio-level metrics:
# {
#   'score_recent': 0.42, 'score_steady': 0.38, 'score_anchor': 0.35,
#   'ic': 0.035, 'ic_sharpe': 1.2, 'hit_rate': 0.58,
#   'mean_return': 0.012, 'max_drawdown': -0.08,
#   'sortino_ratio': 1.5, 'turnover': 0.23,
# }
```

Data is automatically fetched from the coordinator and cached locally.
No coordinator URL or feed configuration needed — baked into the package.

Multi-metric enrichment (IC, hit rate, Sortino, etc.) is computed using the same
metrics registry as the coordinator, giving competitors identical feedback locally.

## Node-private runtime

- `../node/config/` — CrunchConfig, runtime callables
