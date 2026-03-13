# Example Tournament Trackers

These example models demonstrate different prediction strategies for house price prediction.
Each returns `{"prediction": float}` where the value is a predicted price in dollars.

| Tracker | Strategy |
|---------|----------|
| `PricePerSqftTracker` | Multiplies living area by a fixed $/sqft constant |
| `MedianPriceTracker` | Always returns a fixed median price (null-model baseline) |

## Usage

```python
from starter_challenge.examples import PricePerSqftTracker

model = PricePerSqftTracker()
pred = model.predict({"living_area_sqft": 2000.0, "bedrooms": 3.0})
# {"prediction": 350000.0}
```
