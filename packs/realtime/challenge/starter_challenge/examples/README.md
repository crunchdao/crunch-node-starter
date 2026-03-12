# Example Prediction Trackers

Three example models that demonstrate the `feed_update()` + `predict()` interface:

| Model | Strategy | When it works |
|-------|----------|---------------|
| **MomentumTracker** | Projects recent trend forward | Trending markets |
| **MeanReversionTracker** | Bets against deviations from moving average | Range-bound markets |
| **ContrarianTracker** | Fades the last candle's direction | Choppy / mean-reverting markets |

## Interface

All models subclass `ModelBaseClass` and implement two methods:

```python
class MyTracker(ModelBaseClass):
    def feed_update(self, data: dict) -> None:
        """Receive market data. Store state for predict()."""
        super().feed_update(data)  # stores per-subject
        # ... build indicators, etc.

    def predict(self, subject, resolve_horizon_seconds, step_seconds) -> dict:
        """Return {"value": float} — positive=bullish, negative=bearish."""
        data = self._get_data(subject)
        # ... your logic ...
        return {"value": 0.5}
```

## Data Shape

`feed_update()` receives:
```python
{
    "symbol": "BTC",
    "asof_ts": 1700000000,
    "candles_1m": [
        {"ts": ..., "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...},
        ...
    ]
}
```
