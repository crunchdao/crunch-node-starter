# Data Transformation Flow: Feed to Prediction

This document traces how data is transformed from external feed sources through to stored predictions.

## Overview

Data flows through **11 transformation stages** from external feed to stored prediction:

1. **External API** → Raw JSON from Pyth/Binance/etc.
2. **FeedDataRecord** → Normalized immutable record (in-memory)
3. **FeedRecord** → Domain entity with timing metadata (stored in DB)
4. **Raw dict** → FeedReader assembles candle windows
5. **RawInput** → Pydantic validation of feed schema
6. **InputRecord** → Snapshot of input for audit trail (stored in DB)
7. **InferenceInput** → Same as RawInput, sent to models via `feed_update()`
8. **Model.feed_update()** → Models update internal state
9. **Model.predict()** → Models return prediction dict
10. **InferenceOutput** → Pydantic validation of model output
11. **PredictionRecord** → Final record with scope, timing, status (stored in DB)

## Detailed Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                            DATA TRANSFORMATION FLOW: FEED TO PREDICTION                                 │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 1: EXTERNAL DATA SOURCE                                                                   │    │
│  │  (Pyth/Binance/MongoDB)                                                                          │    │
│  │                                                                                                   │    │
│  │  Raw API Response (e.g., Pyth Hermes):                                                           │    │
│  │  {                                                                                                │    │
│  │    "id": "0xe62df6c8b4...",                                                                      │    │
│  │    "price": { "price": "9764523", "expo": -2, "publish_time": 1709753400 }                       │    │
│  │  }                                                                                                │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                                        crunch_node/feeds/providers/pyth.py │               │
│                                                                            PythFeed.fetch() ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 2: FeedDataRecord (Immutable dataclass)                                                   │    │
│  │  crunch_node/feeds/contracts.py:50                                                               │    │
│  │                                                                                                   │    │
│  │  FeedDataRecord(                                                                                  │    │
│  │    source="pyth",                                                                                │    │
│  │    subject="BTC",                                                                                │    │
│  │    kind="tick" | "candle",                                                                       │    │
│  │    granularity="1s",                                                                             │    │
│  │    ts_event=1709753400,  # Unix timestamp                                                        │    │
│  │    values={"price": 97645.23} | {"open": ..., "high": ..., "low": ..., "close": ..., "volume": 0}│    │
│  │    metadata={}                                                                                   │    │
│  │  )                                                                                                │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                                     crunch_node/services/feed_data.py:255 │               │
│                                                                        _feed_to_domain()  ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 3: FeedRecord (Domain dataclass - stored in DB)                                           │    │
│  │  crunch_node/entities/feed_record.py:13                                                          │    │
│  │                                                                                                   │    │
│  │  FeedRecord(                                                                                      │    │
│  │    source="pyth",                                                                                │    │
│  │    subject="BTC",                                                                                │    │
│  │    kind="tick",                                                                                  │    │
│  │    granularity="1s",                                                                             │    │
│  │    ts_event=datetime(2026, 3, 6, ...),  # Converted to datetime                                  │    │
│  │    values={"price": 97645.23},                                                                   │    │
│  │    meta={"timing": {"feed_received_us": ..., "feed_normalized_us": ...}},                        │    │
│  │    ts_ingested=datetime.now(UTC)                                                                 │    │
│  │  )                                                                                                │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                                         DB: feed_records table ◄───────────┘               │
│                                                                    │                                       │
│                                    PG NOTIFY "new_feed_data" with timing payload                           │
│                                                                    │                                       │
│                                                                    ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 4: FeedReader.get_input() → Raw dict                                                      │    │
│  │  crunch_node/services/feed_reader.py:55                                                          │    │
│  │                                                                                                   │    │
│  │  {                                                                                                │    │
│  │    "symbol": "BTC",                                                                              │    │
│  │    "asof_ts": 1709753400,                                                                        │    │
│  │    "candles_1m": [                                                                               │    │
│  │      {"ts": 1709753340, "open": 97640.0, "high": 97650.0, "low": 97630.0, "close": 97645.0, ...},│    │
│  │      {"ts": 1709753400, "open": 97645.0, "high": 97660.0, "low": 97640.0, "close": 97655.0, ...} │    │
│  │    ]                                                                                              │    │
│  │  }                                                                                                │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                            crunch_node/services/predict.py:119            │               │
│                                              config.raw_input_type.model_validate()       ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 5: RawInput (Pydantic model - validated)                                                  │    │
│  │  crunch_node/crunch_config.py:24 (base) or packs/.../crunch_config.py (custom)                   │    │
│  │                                                                                                   │    │
│  │  class RawInput(BaseModel):              # After .model_validate():                              │    │
│  │      symbol: str = "BTC"                 {                                                        │    │
│  │      asof_ts: int = 0                       "symbol": "BTC",                                      │    │
│  │      candles_1m: list[dict]                 "asof_ts": 1709753400,                                │    │
│  │                                             "candles_1m": [...]                                   │    │
│  │                                          }                                                        │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                                     .model_dump() → InputRecord.raw_data  │               │
│                                                                                            ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 6: InputRecord (Domain dataclass - stored in DB)                                          │    │
│  │  crunch_node/entities/prediction.py:24                                                           │    │
│  │                                                                                                   │    │
│  │  InputRecord(                                                                                     │    │
│  │    id="INP_20260306_123045.123",                                                                 │    │
│  │    raw_data={                           # Same dict as RawInput.model_dump()                     │    │
│  │      "symbol": "BTC",                                                                            │    │
│  │      "asof_ts": 1709753400,                                                                      │    │
│  │      "candles_1m": [...]                                                                         │    │
│  │    },                                                                                             │    │
│  │    received_at=datetime.now(UTC),                                                                │    │
│  │    _timing={"feed_received_us": ..., "data_loaded_us": ...}                                      │    │
│  │  )                                                                                                │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                                         DB: inputs table ◄─────────────────┘               │
│                                                                                                            │
│                              ═══════════════════════════════════════════════════════════                   │
│                                               MODEL INVOCATION PATH                                        │
│                              ═══════════════════════════════════════════════════════════                   │
│                                                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 7: InferenceInput (Pydantic model - what models see)                                      │    │
│  │  crunch_node/crunch_config.py:36 (typically same as RawInput)                                    │    │
│  │                                                                                                   │    │
│  │  class InferenceInput(RawInput):         # feed_update() receives this as dict:                  │    │
│  │      pass   # Same fields as RawInput    {"symbol": "BTC", "asof_ts": ..., "candles_1m": [...]}  │    │
│  │                                                                                                   │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                                        gRPC call via PredictionKernel     │               │
│                                                  crunch_node/services/predict_components.py           │
│                                                        encode_feed_update()              │               │
│                                                                                            ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 8: Model.feed_update(data) - State update                                                 │    │
│  │  scaffold/challenge/starter_challenge/cruncher.py                                                 │    │
│  │                                                                                                   │    │
│  │  def feed_update(self, data: dict[str, Any]) -> None:                                            │    │
│  │      # data = {"symbol": "BTC", "asof_ts": ..., "candles_1m": [...]}                             │    │
│  │      self._latest_data_by_subject[data.get("symbol", "_default")] = data                         │    │
│  │                                                                                                   │    │
│  │  # Models maintain internal state (indicators, history, etc.)                                    │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                                        gRPC call via PredictionKernel     │               │
│                                              crunch_node/services/predict_components.py:320               │
│                                                        encode_predict(scope)              │               │
│                                                                                            ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 9: Model.predict(subject, resolve_horizon_seconds, step_seconds) → dict                   │    │
│  │  scaffold/challenge/starter_challenge/cruncher.py:50                                              │    │
│  │                                                                                                   │    │
│  │  def predict(self, subject: str, resolve_horizon_seconds: int, step_seconds: int) -> dict:       │    │
│  │      # Access latest data via self._get_data(subject)                                            │    │
│  │      # Run model logic                                                                           │    │
│  │      return {"value": 0.75}  # Positive = bullish, negative = bearish                            │    │
│  │                                                                                                   │    │
│  │  # Scope args come from CallMethodConfig in CrunchConfig                                         │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                        crunch_node/services/predict.py:289               │               │
│                                             _map_runner_result() + validate_output()      ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 10: InferenceOutput (Pydantic model - validated response)                                 │    │
│  │  crunch_node/crunch_config.py:42 (base) or packs/.../crunch_config.py (custom)                   │    │
│  │                                                                                                   │    │
│  │  class InferenceOutput(BaseModel):       # After OutputValidator.validate_and_normalize():       │    │
│  │      value: float = 0.0                  {"value": 0.75}                                          │    │
│  │                                                                                                   │    │
│  │  # Validation ensures output matches expected schema                                             │    │
│  │  # Fails FAST if model returns wrong format                                                      │    │
│  └────────────────────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                                            │               │
│                                  crunch_node/services/predict_components.py:146          │               │
│                                            PredictionRecordFactory.build()                ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  STAGE 11: PredictionRecord (Domain dataclass - stored in DB)                                    │    │
│  │  crunch_node/entities/prediction.py:37                                                           │    │
│  │                                                                                                   │    │
│  │  PredictionRecord(                                                                                │    │
│  │    id="PRE_model123_BTC-60_20260306_123045.123",                                                 │    │
│  │    input_id="INP_20260306_123045.123",      # Links to InputRecord                               │    │
│  │    model_id="model123",                                                                          │    │
│  │    prediction_config_id="prediction-btc-60s",                                                    │    │
│  │    scope_key="BTC-60",                                                                           │    │
│  │    scope={"subject": "BTC", "resolve_horizon_seconds": 60, "step_seconds": 15},                  │    │
│  │    status=PredictionStatus.PENDING,                                                              │    │
│  │    exec_time_ms=12.5,                                                                            │    │
│  │    inference_output={"value": 0.75},       # ◄─── FINAL OUTPUT                                   │    │
│  │    meta={"timing": {"feed_received_us": ..., "models_completed_us": ...}},                       │    │
│  │    performed_at=datetime.now(UTC),                                                               │    │
│  │    resolvable_at=datetime.now(UTC) + timedelta(seconds=60)                                       │    │
│  │  )                                                                                                │    │
│  └─────────────────────────────────────────────────────────────────────────────────────────────────┘    │
│                                                           │                                              │
│                                     DB: predictions table ◄┘                                              │
│                                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Pydantic Model Relationships

All defined in `crunch_node/crunch_config.py` (defaults) or `packs/.../crunch_config.py` (custom):

```
┌──────────────────┐      inherits       ┌──────────────────┐      inherits       ┌──────────────────┐
│    BaseModel     │◄────────────────────│     RawInput     │◄────────────────────│  InferenceInput  │
│   (pydantic)     │                     │  (feed schema)   │                     │ (model receives) │
└──────────────────┘                     └──────────────────┘                     └──────────────────┘
        ▲                                        ▲
        │                                        │ inherits
┌──────────────────┐                     ┌──────────────────┐
│  InferenceOutput │                     │   GroundTruth    │
│ (model returns)  │                     │  (actuals for    │
│   {value: float} │                     │   scoring)       │
└──────────────────┘                     └──────────────────┘

┌──────────────────┐
│   ScoreResult    │
│ (scoring output) │
│ {value, success} │
└──────────────────┘
```

CrunchConfig holds type references:

| Config Field | Default Type | Purpose |
|--------------|--------------|---------|
| `raw_input_type` | `RawInput` | Feed data schema |
| `ground_truth_type` | `GroundTruth` (= RawInput) | Actuals for scoring |
| `input_type` | `InferenceInput` (= RawInput) | What models receive |
| `output_type` | `InferenceOutput` | What models return |
| `score_type` | `ScoreResult` | Scoring output |

## Key Files by Transformation Stage

| Stage | Files |
|-------|-------|
| Feed Provider → FeedDataRecord | `crunch_node/feeds/providers/pyth.py`, `crunch_node/feeds/contracts.py` |
| FeedDataRecord → FeedRecord (DB) | `crunch_node/services/feed_data.py`, `crunch_node/entities/feed_record.py` |
| FeedRecord → RawInput dict | `crunch_node/services/feed_reader.py` |
| RawInput → InputRecord | `crunch_node/services/predict.py`, `crunch_node/crunch_config.py`, `crunch_node/entities/prediction.py` |
| Model invocation | `crunch_node/services/predict_components.py`, `crunch_node/services/realtime_predict.py` |
| Model response → PredictionRecord | `crunch_node/services/predict.py`, `crunch_node/services/predict_components.py`, `crunch_node/entities/prediction.py` |

## Feed Data Kind

The `kind` field in feed records controls both data shape and temporal semantics:

| Kind | Event Semantics | Value Shape | Timestamp |
|------|-----------------|-------------|-----------|
| `tick` | Single price observation | `{"price": 97645.23}` | Exact event time |
| `candle` | Aggregated OHLCV | `{"open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}` | Bucketed to granularity |

Note: Some providers (like Pyth) only supply ticks natively. When `kind="candle"` is requested, they reshape the single price into OHLCV format with `open=high=low=close=price` and `volume=0`. Real candles (with meaningful OHLC spread) come from providers like Binance.

The `granularity` field (`1s`, `1m`, `5m`, etc.) specifies the time bucket size for candles.

---

## Proposed Architecture: Feed-Defined Shapes

The current architecture has redundant Pydantic models and transforms data between kinds. The proposed architecture simplifies this by having feeds define their output shapes directly.

### Current Problems

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    CURRENT ARCHITECTURE                                      │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  EXTERNAL FEED (Pyth/Binance)                                                        │    │
│  │  {"price": 97645.23} or {"open": ..., "high": ..., ...}                             │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                          FeedDataRecord      ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  FeedDataRecord (contracts.py)                                                       │    │
│  │  {source, subject, kind="tick"|"candle", granularity, ts_event, values}             │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                          _feed_to_domain()   ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  FeedRecord (entities/feed_record.py) — stored in DB                                 │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                         FeedReader + CandleNormalizer        ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  CandleNormalizer.normalize()                                                        │    │
│  │                                                                                       │    │
│  │  PROBLEM: TRANSFORMS tick -> fake candle:                                            │    │
│  │      if kind == "candle": use OHLCV                                                  │    │
│  │      else: open=high=low=close=price, volume=0                                       │    │
│  │                                                                                       │    │
│  │  Returns: CandleInput {symbol, asof_ts, candles_1m: [Candle]}                        │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                               config.raw_input_type.model_validate()         ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  RawInput (crunch_config.py) — DUPLICATE VALIDATION                                  │    │
│  │  {symbol: str, asof_ts: int, candles_1m: list[dict]}                                │    │
│  │                                                                                       │    │
│  │  PROBLEM: Same shape as CandleInput but separate Pydantic model                      │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                                              ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  InputRecord (entities/prediction.py) — stored in DB                                 │    │
│  │  {id, raw_data: dict, received_at}                                                   │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                feed_update() receives dict    ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  InferenceInput (crunch_config.py) — REDUNDANT                                       │    │
│  │  class InferenceInput(RawInput): pass  <- just an alias                             │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                              Model.feed_update(data)          ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  Model receives: {"symbol": "BTC", "asof_ts": ..., "candles_1m": [...]}             │    │
│  │                                                                                       │    │
│  │  PROBLEM: Always candle-shaped, even if source was ticks                             │    │
│  └─────────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                              │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  PROBLEMS:                                                                                   │
│  * CandleNormalizer transforms ticks -> fake candles (violates pass-through principle)      │
│  * RawInput duplicates CandleInput shape                                                    │
│  * InferenceInput is just an empty subclass of RawInput                                     │
│  * Feed kind doesn't determine output shape — everything becomes candles                    │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    PROPOSED ARCHITECTURE                                     │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  EXTERNAL FEED                                                                       │    │
│  │                                                                                       │    │
│  │  Binance (kind=candle): {"open": 97640, "high": 97680, "low": 97620, "close": 97660}│    │
│  │  Pyth (kind=tick):      {"price": 97645.23}                                         │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                          FeedDataRecord      ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  FeedDataRecord (contracts.py)                                                       │    │
│  │  {source, subject, kind, granularity, ts_event, values}                             │    │
│  │                                                                                       │    │
│  │  PASS-THROUGH: values unchanged from source                                          │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                          _feed_to_domain()   ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  FeedRecord (entities/feed_record.py) — stored in DB                                 │    │
│  │                                                                                       │    │
│  │  PASS-THROUGH: values unchanged                                                      │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                              ┌───────────────────────────────────────────────┘               │
│                              │                                                               │
│              FEED_KIND=candle│                            FEED_KIND=tick                     │
│                              ▼                                   │                           │
│  ┌──────────────────────────────────────────┐                   │                           │
│  │  CandleNormalizer                        │                   ▼                           │
│  │  normalizers/candle.py                   │    ┌──────────────────────────────────────┐   │
│  │                                          │    │  TickNormalizer                      │   │
│  │  Only handles kind="candle"              │    │  normalizers/tick.py (NEW)           │   │
│  │  Rejects/skips ticks                     │    │                                      │   │
│  │                                          │    │  Only handles kind="tick"            │   │
│  │  output_type = CandleInput               │    │  Rejects/skips candles               │   │
│  │  {symbol, asof_ts, candles_1m: [Candle]} │    │                                      │   │
│  │                                          │    │  output_type = TickInput             │   │
│  │  Candle:                                 │    │  {symbol, asof_ts, ticks: [Tick]}    │   │
│  │    {ts, open, high, low, close, volume}  │    │                                      │   │
│  └──────────────────────────────────────────┘    │  Tick:                               │   │
│                              │                   │    {ts, price}                       │   │
│                              │                   └──────────────────────────────────────┘   │
│                              │                                   │                           │
│                              └───────────────┬───────────────────┘                           │
│                                              │                                               │
│                         normalizer.output_type (NO EXTRA VALIDATION)                        │
│                                              ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  InputRecord — stored in DB                                                          │    │
│  │  {id, raw_data: normalizer.output_type.model_dump(), received_at}                   │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                                              │               │
│                                        Model.feed_update(data)                ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  Model receives normalizer.output_type as dict                                       │    │
│  │                                                                                       │    │
│  │  If FEED_KIND=candle:  {"symbol": "BTC", "candles_1m": [{ts, open, high, low, ...}]}│    │
│  │  If FEED_KIND=tick:    {"symbol": "BTC", "ticks": [{ts, price}, ...]}               │    │
│  │                                                                                       │    │
│  │  Shape matches what feed actually provides                                           │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                    Model.predict()           ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  InferenceOutput (crunch_config.py) — STAYS                                          │    │
│  │  {value: float}                                                                      │    │
│  └──────────────────────────────────────────────────────────────────────────┬──────────┘    │
│                                                                              │               │
│                                                                              ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  PredictionRecord — stored in DB                                                     │    │
│  └─────────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                              │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  CrunchConfig (after):                                                                       │
│                                                                                              │
│    feed_normalizer: str = "candle"        # picks normalizer, defines input shape           │
│    ground_truth_type: type = GroundTruth  # for scoring (computed, not raw feed)            │
│    output_type: type = InferenceOutput    # what models return                              │
│    score_type: type = ScoreResult         # what scoring produces                           │
│                                                                                              │
│  REMOVED:                                                                                    │
│    raw_input_type     ─┐                                                                    │
│    input_type         ─┼─> replaced by normalizer.output_type                               │
│    InferenceInput     ─┘                                                                    │
│                                                                                              │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  BENEFITS:                                                                                   │
│  * No transformation — feed shape passes through unchanged                                  │
│  * No duplicate Pydantic models — normalizer owns the shape                                 │
│  * Explicit — FEED_KIND determines what models receive                                      │
│  * Extensible — add normalizers without touching CrunchConfig                               │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Summary of Changes

| Remove | Keep | Add |
|--------|------|-----|
| `raw_input_type` | `ground_truth_type` | `TickNormalizer` |
| `input_type` | `output_type` | `TickInput` / `Tick` |
| `InferenceInput` class | `score_type` | `feed_normalizer` config field |
| `RawInput` class | `InferenceOutput` | |
| tick->candle transformation | `ScoreResult` | |

### Adding a New Normalizer

To add a new normalizer (e.g., for orderbook depth):

**1. Create the normalizer file** (`crunch_node/feeds/normalizers/depth.py`):
```python
from pydantic import BaseModel

class DepthLevel(BaseModel):
    price: float
    quantity: float

class DepthInput(BaseModel):
    symbol: str
    asof_ts: int
    bids: list[DepthLevel]
    asks: list[DepthLevel]

class DepthNormalizer:
    output_type = DepthInput

    def normalize(self, records, subject) -> DepthInput:
        # Only handles kind="depth"
        ...
```

**2. Register in `__init__.py`**:
```python
NORMALIZERS = {
    "candle": CandleNormalizer,
    "tick": TickNormalizer,
    "depth": DepthNormalizer,  # add this line
}
```

**3. Use it**:
```python
# In CrunchConfig or env
feed_normalizer = "depth"
```
