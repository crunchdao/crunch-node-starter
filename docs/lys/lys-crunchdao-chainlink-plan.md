# LYS × CrunchDAO × Chainlink — CrunchDAO Execution Plan

## What CrunchDAO Actually Builds

CrunchDAO's role across all three tracks is **signal intelligence** — we run competitions where models predict market microstructure events, and the winning signals feed into LYS's execution layer.

This means **two coordinator nodes** (Track 1 and Track 3), plus a **signal API contract** with LYS for live consumption. Track 2 is mostly LYS/Auros execution — CrunchDAO has no direct competition role there (but could add funding-rate signal prediction later).

---

## The Two Crunches

### Crunch A — "CEX-DEX Correction Predictor" (Track 1)

**What models predict:** Probability that a DEX price will correct toward CEX price within 200–800ms, given a detected divergence.

| Dimension | Value |
|---|---|
| **Feed source** | Chainlink Data Streams (CEX prices) + DEX pool state (Raydium/Orca) |
| **Feed subjects** | SOL, ETH, BTC (top liquid pairs — 10+ at gate) |
| **Feed granularity** | 100–200ms (sub-second — requires custom feed worker) |
| **Input to models** | CEX velocity, volume spike magnitude, pool depth, spread width, regime label |
| **Output from models** | `{correction_probability: float, direction: str, expected_magnitude_bps: float}` |
| **Ground truth** | Did DEX price correct ≥X bps within 800ms? Entry/exit price, actual magnitude |
| **Scoring** | Brier score on correction probability + profit-weighted accuracy on direction |
| **Resolve horizon** | ~1–2 seconds (capture the 800ms window + buffer) |
| **Prediction interval** | Event-driven (on divergence detection), not fixed interval |
| **Accuracy threshold** | >55% on qualifying corrections (divergence > threshold) |

**⚠️ Architecture challenge:** The standard coordinator node pipeline assumes fixed-interval predictions on 1s+ feed granularity. Track 1 needs:
1. **Sub-second feed ingestion** — custom feed worker polling Chainlink Data Streams at 100ms+
2. **Event-driven predictions** — trigger on divergence detection, not on fixed timer
3. **Sub-second ground truth resolution** — resolve within 1–2s

**Options:**
- **Option A:** Extend coordinator-node with event-driven prediction mode (new `ScheduledPrediction` trigger type)
- **Option B:** Run a sidecar service that detects divergences, calls models, logs to coordinator for scoring only
- **Option C:** Use coordinator for offline/batch scoring on historical divergence events; live signal runs outside

**Recommendation:** Start with **Option C** (offline competition on historical data), graduate to **Option A** once latency validation passes at Month 4. This lets models train on real data without building sub-second infra upfront.

---

### Crunch B — "Meteora LP Direction Signal" (Track 3)

**What models predict:** Short-term (30–180s) price direction on Raydium based on large Meteora DLMM LP bin shifts.

| Dimension | Value |
|---|---|
| **Feed source** | Meteora DLMM pool state (LYS Core already decodes this) |
| **Feed subjects** | Top Meteora pools (SOL/USDC, etc.) |
| **Feed granularity** | 1–5s (LP bin snapshots) |
| **Input to models** | LP bin positions, bin shift magnitude, pool depth changes, volume |
| **Output from models** | `{direction: str, confidence: float, magnitude_bps: float}` |
| **Ground truth** | Raydium price change over 30–180s after qualifying LP move |
| **Scoring** | Directional accuracy + confidence calibration + profit metric |
| **Resolve horizon** | 30–180 seconds |
| **Prediction interval** | Event-driven (on LP shift > $100k) or fixed 5–10s |
| **Accuracy threshold** | >60% directional accuracy on qualifying LP moves (>$100k) |

**✅ Architecture fit:** This maps cleanly to the existing coordinator node:
- Feed granularity (1–5s) < resolve horizon (30–180s) ✓
- Fixed prediction intervals work ✓
- Standard scoring pipeline works ✓
- Can start with fixed-interval, add event-driven later

---

## What We Do NOT Build

| Item | Owner | Why not CrunchDAO |
|---|---|---|
| LYS Flash execution | LYS | Execution infra, not signal |
| Jito bundle submission | LYS | MEV execution layer |
| Drift perps integration | LYS | Track 2 execution |
| Auros capital management | LYS/Auros | Capital, not signal |
| Chainlink Data Streams ingestion | LYS (we consume) | They own the data pipe; we receive via API/feed |

**CrunchDAO delivers signal. LYS executes on signal. Clean boundary.**

---

## Implementation Sequence

### Phase 0 — Signal API Contract (Week 1)

Before building either crunch, agree with LYS on the **signal consumption interface**:

```
CrunchDAO Coordinator → Signal API → LYS Execution Layer
```

Define:
- API format (REST? WebSocket? gRPC?)
- Latency SLA (<100ms as stated in doc)
- Payload schema (which model? ensemble? confidence threshold?)
- Authentication
- Failover behavior (what does LYS do if signal is unavailable?)

**This is the contract that makes everything downstream valuable.**

### Phase 1 — Crunch B: Meteora LP Direction (Weeks 1–4)

**Why first:** It fits the coordinator node architecture out-of-the-box, LYS Core already decodes Meteora state, and it's independent of the Chainlink gate. This is our "Track 2 equivalent" — we ship something real fast.

#### Week 1–2: Scaffold + Types
1. `crunch-cli init-workspace meteora-lp-signal`
2. Define types:
   - `RawInput`: LP bin snapshot (positions, depths, volumes, timestamps)
   - `InferenceInput`: Processed features (shift magnitude, bin concentration, velocity)
   - `InferenceOutput`: `{direction: "up"|"down"|"neutral", confidence: float, magnitude_bps: float}`
   - `GroundTruth`: `{actual_direction: str, actual_magnitude_bps: float, entry_price: float, exit_price: float}`
   - `ScoreResult`: `{directional_accuracy: float, profit_bps: float, calibration_error: float, value: float}`
3. Define tracker interface
4. Build 3–5 example models (simple heuristics: bin-shift momentum, depth imbalance, mean-reversion)
5. `make test` ✓

#### Week 2–3: Feed + Scoring
1. Custom feed worker to ingest Meteora DLMM state from LYS Core API
2. `resolve_ground_truth`: fetch Raydium price at prediction time + 30–180s later
3. Scoring function: directional accuracy × confidence calibration
4. `make test` ✓, `make deploy` ✓, `make verify-e2e` ✓

#### Week 3–4: Stage 1 Rally
1. Register coordinator on-chain
2. Open competition to CrunchDAO community
3. Seed with example models
4. Monitor: are models producing >60% accuracy on qualifying moves?
5. Connect signal API to LYS for paper-trading validation

**Deliverable:** Live competition producing directional signals on Meteora LP shifts.

### Phase 2 — Chainlink Data Collection + Offline Crunch A (Weeks 2–8)

**Parallel with Phase 1.** While Crunch B runs live, build the dataset and offline competition for Track 1.

#### Week 2–4: Data Pipeline
1. Ingest Chainlink Data Streams (CEX prices) — store raw
2. Ingest DEX pool state (Raydium/Orca) — store raw
3. Build divergence detection logic (CEX price vs DEX price, threshold-based)
4. Label historical events: divergence detected → did correction happen within 800ms?
5. Package as competition dataset (parquet files with features + labels)

#### Week 4–6: Offline Competition
1. Scaffold workspace: `crunch-cli init-workspace cex-dex-predictor`
2. Types oriented around the divergence event:
   - `InferenceInput`: `{cex_velocity: float, volume_spike: float, pool_depth: dict, spread_bps: float, regime: str}`
   - `InferenceOutput`: `{correction_probability: float, direction: str, expected_magnitude_bps: float}`
3. Backtest-style scoring on historical dataset
4. Stage 1 Rally on historical data — find models that beat 55% threshold

#### Week 6–8: Latency Validation (aligns with Month 2–3 in business plan)
1. Paper-trade top models against live Chainlink data
2. Measure: model inference latency + signal delivery latency
3. Target: end-to-end <100ms (model inference + API delivery)

### Phase 3 — Chainlink Gate Decision (Month 4)

This is the **binary gate** from the business plan. CrunchDAO's inputs to the decision:

| Metric | Target | How we measure |
|---|---|---|
| Model accuracy on corrections | >55% | Live paper-trading results from Phase 2 |
| Signal delivery latency | <100ms | API instrumentation |
| Model ensemble stability | Low variance | Score distribution across top 5 models |
| Coverage (pairs) | 10+ liquid pairs | Feed subjects with sufficient divergence events |

**If PASS:**
- Upgrade Crunch A to live (event-driven predictions on real divergences)
- Extend coordinator-node with event-driven prediction trigger
- Connect signal API to LYS for live execution
- Scale to Stage 2 Continuous Competition

**If FAIL:**
- Do not deploy live
- Continue Crunch B (Meteora)
- Investigate: is it latency? accuracy? data quality?
- Potentially rebuild signal architecture (different features, different model interface)

### Phase 4 — Continuous Competition + Model Rotation (Month 5+)

For both crunches:
1. Weekly model refresh (new submissions scored against recent data)
2. Quarterly rotation (retire decaying models, promote new ones)
3. Live PnL-weighted model selection (ensemble weights based on recent profit)
4. Alpha decay monitoring: track accuracy degradation over time

---

## Coordinator Node Changes Required

| Change | Effort | Which Crunch |
|---|---|---|
| Custom feed worker for Meteora DLMM state | Medium | Crunch B |
| Custom feed worker for Chainlink Data Streams | Medium | Crunch A |
| Event-driven prediction trigger (not just fixed interval) | Large | Crunch A (live mode) |
| Backtest/offline competition mode (score on historical dataset) | Medium | Crunch A (initial) |
| Signal API endpoint (expose ensemble prediction via REST/WS) | Medium | Both |
| Sub-second feed granularity support | Medium | Crunch A |

### Priority build order:
1. **Meteora feed worker** — unblocks Crunch B entirely
2. **Signal API endpoint** — needed for LYS consumption of both crunches
3. **Chainlink feed worker** — unblocks data collection for Crunch A
4. **Offline/backtest scoring mode** — run Crunch A on historical data
5. **Event-driven predictions** — only needed if Chainlink gate passes

---

## Key Decisions Needed from You

1. **Signal API format:** REST polling? WebSocket streaming? gRPC? What does LYS prefer?
2. **Meteora data access:** Does LYS provide a real-time API for decoded DLMM state, or do we decode on-chain ourselves?
3. **Chainlink Data Streams access:** Do we get direct API access, or does LYS proxy the data to us?
4. **Capital for Crunch rewards:** What's the prize pool for each competition? (Drives participant engagement)
5. **Track 2 signal component:** Is there a CrunchDAO role in funding rate prediction, or is T2 purely LYS/Auros execution?
6. **Offline vs live for Crunch A initially:** Confirm we start with historical dataset competition, not live sub-second predictions

---

## Risk Matrix (CrunchDAO-specific)

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Meteora data too noisy for 60% accuracy | Crunch B fails | Medium | Validate with simple heuristics before opening competition |
| Model inference latency >100ms | Signal unusable for T1 | Medium | Profile models, set hard latency cap, run inference on GPU |
| Alpha decay faster than model rotation | Edge disappears | Medium | Weekly refresh, PnL-weighted ensemble, regime detection |
| Too few participants | Weak signal diversity | Low | Seed with internal models, attractive prize pool |
| LYS API changes break feed workers | Pipeline stops | Low | Version the API contract, integration tests |
| Sub-second feed not yet implemented | Crunch A blocked | Medium | Extend coordinator node with fast-path for sub-second events |

---

## Timeline Summary

```
Week 1      Signal API contract with LYS
            Scaffold Crunch B (Meteora LP)
Week 2-3    Crunch B feed + scoring + deploy
            Start Chainlink data collection (parallel)
Week 3-4    Crunch B Stage 1 Rally (live competition)
            Paper-trade Crunch B signals
Week 4-6    Crunch A offline competition on historical data
            Stage 1 Rally for CEX-DEX models
Week 6-8    Live paper-trading top Crunch A models
            Latency validation
Month 4     CHAINLINK GATE DECISION
            Pass → Crunch A goes live
            Fail → Continue Crunch B, rebuild T1
Month 5+    Stage 2 Continuous Competition (both)
            Model rotation, alpha decay monitoring
            Track 3 Crunch B becomes standalone revenue
```

---

## Success Criteria

| Milestone | Metric | Deadline |
|---|---|---|
| Crunch B live with participants | ≥5 models submitting | Week 4 |
| Crunch B signal >60% directional accuracy | Measured on live data | Week 6 |
| Signal API delivering to LYS | <100ms latency, >99% uptime | Week 4 |
| Crunch A historical dataset packaged | ≥10k labeled divergence events | Week 6 |
| Crunch A models >55% accuracy | Measured on historical + paper trading | Month 3 |
| Chainlink gate decision made | Binary pass/fail with data | Month 4 |
| Stage 2 rotation architecture live | Weekly refresh, PnL-weighted ensemble | Month 5 |
