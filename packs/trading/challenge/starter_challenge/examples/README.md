# Example Trading Trackers

These example models demonstrate different trading signal strategies.
Each returns `{"signal": float}` in [-1, 1].

| Tracker | Strategy | When it works |
|---------|----------|---------------|
| `MomentumTracker` | Projects recent trend forward | Trending markets |
| `MeanReversionTracker` | Fades short-term overextension | Range-bound markets |
| `BreakoutTracker` | Enters on range expansion | Breakout/trending transitions |

## Usage

```python
from starter_challenge.examples import MomentumTracker

model = MomentumTracker()
model.tick(market_data)
signal = model.predict("BTCUSDT", resolve_horizon_seconds=60, step_seconds=15)
# {"signal": 0.42}
```
