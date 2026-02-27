# Example Tournament Trackers

These example models demonstrate different prediction strategies.
Each returns `{"prediction": float}`.

| Tracker | Strategy | When it works |
|---------|----------|---------------|
| `FeatureMomentumTracker` | Uses features or price momentum | When features carry signal |
| `LinearComboTracker` | Equal-weight average of all features | Strong baseline |
| `ContrarianTracker` | Inverts the feature signal | Mean-reverting targets |

## Usage

```python
from starter_challenge.examples import LinearComboTracker

model = LinearComboTracker()
model.tick(feature_data)
pred = model.predict("BTC", resolve_horizon_seconds=3600, step_seconds=300)
# {"prediction": 0.00234}
```
