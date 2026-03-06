# Feed Normalizer Design

**Date:** 2026-03-05
**Status:** Draft
**Goal:** Extract feed-to-input transformation into pluggable normalizers, eliminating duplication and enabling non-financial feed types.

## Background

The `if record.kind == "candle"` logic is duplicated in 4 places:
- `feed_reader.py:124` and `feed_reader.py:236`
- `feed_window.py:80`
- `pyth.py:253`

All produce the same candle format, with tick data converted to "fake candles" (price → OHLCV with equal values). This couples the entire feed system to financial data formats.

## Decision

Introduce a `FeedNormalizer` abstraction:
- One normalizer per output format (not per input kind)
- Contract config declares which normalizer to use
- Default to `CandleNormalizer` for backward compatibility
- Normalizer handles multiple input kinds internally

## Architecture

```
Contract config (feed_normalizer = "candle")
    ↓
FeedReader / FeedWindow
    ↓
normalizer.normalize(records, subject)
    ↓
{"symbol": "BTC", "asof_ts": ..., "candles_1m": [...]}
```

## Components

### FeedNormalizer Protocol

```python
# crunch_node/feeds/normalizers/base.py
class FeedNormalizer(Protocol):
    def normalize(
        self,
        records: Sequence[FeedDataRecord],
        subject: str,
    ) -> dict[str, Any]:
        """Transform feed records into model input format."""
        ...
```

### CandleNormalizer

```python
# crunch_node/feeds/normalizers/candle.py
class CandleNormalizer:
    def normalize(self, records, subject) -> dict[str, Any]:
        candles = [self._record_to_candle(r) for r in records]
        asof_ts = int(records[-1].ts_event) if records else 0
        return {
            "symbol": subject,
            "asof_ts": asof_ts,
            "candles_1m": candles,
        }

    def _record_to_candle(self, record: FeedDataRecord) -> dict[str, Any]:
        # Handles both kind="candle" (real OHLCV) and kind="tick" (price only)
        ...
```

### Registry

```python
# crunch_node/feeds/normalizers/__init__.py
NORMALIZERS: dict[str, type[FeedNormalizer]] = {
    "candle": CandleNormalizer,
}

def get_normalizer(name: str | None = None) -> FeedNormalizer:
    cls = NORMALIZERS.get(name or "candle", CandleNormalizer)
    return cls()
```

## Integration

### FeedReader

```python
class FeedReader:
    def __init__(self, ..., normalizer: FeedNormalizer | None = None):
        self.normalizer = normalizer or get_normalizer()

    def get_input(self, now: datetime) -> dict[str, Any]:
        records = self._load_recent_records(limit=self.window_size)
        result = self.normalizer.normalize(records, self.subject)
        if feed_timing:
            result["_feed_timing"] = feed_timing
        return result
```

### FeedWindow

```python
class FeedWindow:
    def __init__(self, max_size: int = 120, normalizer: FeedNormalizer | None = None):
        self.normalizer = normalizer or get_normalizer()

    def get_input(self, subject: str) -> dict[str, Any]:
        records = list(self._windows.get(subject, []))
        return self.normalizer.normalize(records, subject)
```

### PredictSink

```python
def _build_input(self, subject: str) -> dict[str, Any]:
    return self.feed_window.get_input(subject)
```

## Configuration

```python
# In contract config
class CrunchConfig:
    feed_normalizer: str = "candle"  # default
```

If `feed_normalizer` not set, defaults to `"candle"`. Existing configs work without changes.

## Files Changed

| File | Change |
|------|--------|
| `crunch_node/feeds/normalizers/__init__.py` | New — registry |
| `crunch_node/feeds/normalizers/base.py` | New — Protocol |
| `crunch_node/feeds/normalizers/candle.py` | New — CandleNormalizer |
| `crunch_node/services/feed_reader.py` | Use normalizer, remove candle logic |
| `crunch_node/services/feed_window.py` | Use normalizer, remove `_record_to_candle` |
| `crunch_node/services/predict_sink.py` | Delegate to `feed_window.get_input()` |
| `crunch_node/config/crunch_config.py` | Add `feed_normalizer` field |
| `tests/test_feed_normalizers.py` | New |

### Removed

- `FeedReader.get_latest_candles()` — unused
- `FeedReader._load_recent_candles()` → `_load_recent_records()`
- `FeedWindow._record_to_candle()` — moved to normalizer
- `FeedWindow.get_candles()` — replaced by `get_input()`

## Testing

New test file `tests/test_feed_normalizers.py`:
- `test_candle_normalizer_with_candle_kind`
- `test_candle_normalizer_with_tick_kind`
- `test_candle_normalizer_empty_records`
- `test_registry_returns_default`
- `test_registry_returns_requested_normalizer`

Updated tests:
- `test_feed_window.py` — use `get_input()` instead of `get_candles()`
- `test_predict_sink.py` — verify normalizer is called

## Future Extensions

To add a new normalizer (e.g., for sensor data):

1. Create `crunch_node/feeds/normalizers/sensor.py` with `SensorNormalizer`
2. Register in `__init__.py`: `NORMALIZERS["sensor"] = SensorNormalizer`
3. Set `feed_normalizer = "sensor"` in contract config
