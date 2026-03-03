# LYS × CrunchDAO — Competition Design Spec

**Status**: Draft — internal review before sharing with LYS Labs

**Source documents**:
- `lys_three_track_plan.docx` — Three-Track Revenue Strategy (Track 1/2/3 definitions, SLAs, Chainlink gate)
- `Crunch × LysLab LowFreq Alpha Engine.pdf` — LYS Flash pipeline specs, submission pathways, Enigma feature space, data obfuscation, revenue architecture

---

## 1. The Competitions

### Competition 1: CEX-DEX Correction Predictor (Track 1)

Chainlink Data Streams delivers verified CEX prices to Solana before Raydium and Meteora pools correct. When BTC moves $300 on Binance, the corresponding DEX pool hasn't repriced yet. Models predict which of these dislocations will correct, in what direction, and with what magnitude — within a 200–800ms window. LYS Flash executes on high-confidence signals before the pool reprices. The competition targets 15–25 liquid CEX-correlated pairs (SOL/USDC, wBTC/USDC, wETH/USDC, top mid-caps) with ≥$500k TVL. This is the primary revenue driver — per-trade economics are 2–3 orders of magnitude above the $1.58 on-chain arb baseline — but it's gated on a hard Chainlink latency validation at Month 4.

### Competition 2: Meteora LP Behavior Classifier (Track 3)

Meteora DLMM requires LPs to actively manage concentrated liquidity in specific price bins. When a large LP adds, removes, or shifts positions, they're making a deliberate capital allocation decision with a view on near-term price direction. LYS Core decodes Meteora pool state at 14ms — meaning these position changes are visible before they influence price. Models classify whether a given LP position change predicts an upward or downward correction on the corresponding Raydium pair in the next 30–180 seconds. The signal runs on existing Track 1 infrastructure at near-zero marginal cost and is structurally uncorrelated to Track 1 PnL.

### Potential Competition 3: Low-Frequency Alpha on Graduated Tokens

The LowFreq Alpha Engine PDF describes a broader alpha discovery platform targeting graduated tokens on Raydium. Models receive obfuscated on-chain behavioral features from Enigma (50+ parameters: holder dynamics, dev activity, volume metrics, graduation events, technical indicators) and submit return distributions or directional probabilities. This is a distinct competition from Tracks 1 and 3 — lower frequency, wider asset universe (hundreds of graduated tokens vs 15–25 liquid pairs), and uses Enigma's behavioral microstructure data rather than CEX prices or LP position changes.

**Status**: Not in the three-track plan. The LowFreq PDF was a separate/parallel proposal. Needs clarification on whether this is a fourth competition or an alternative framing for Tracks 1/3. See Question 15 below.

---

## 2. SLAs

### Competition 1: CEX-DEX Correction Predictor

The alpha window is 200–800ms. The current coordinator node pipeline isn't optimized for this latency, but the architecture can be extended to support it. Live signal delivery will likely need a fast-path that bypasses the standard DB write → pg NOTIFY flow, while the coordinator continues to handle scoring and competition management asynchronously.

**LYS Flash pipeline budget** (from LowFreq PDF benchmarks):
- TX Build (Rust SDK): <100µs
- TEE Signing (AMD SEV-SNP, VSOCK): <10ms
- Transport to Mempool: 225ms P50 (566ms P50 confirm)
- **Total LYS execution: ~236ms**

This means: of a 200–500ms alpha window, LYS needs ~236ms for execution. CrunchDAO gets the **time before that** — the signal must arrive before LYS starts building the transaction. If the alpha window is 500ms total, CrunchDAO has ~264ms. If 200ms, the window is essentially zero. **This constrains us to the larger divergence events (>300ms windows) unless LYS can pipeline signal receipt with TX build.**

| SLA | Target | Rationale |
|---|---|---|
| **End-to-end signal latency** (divergence detected → signal delivered to LYS) | <100ms | LYS needs ~236ms for execution. On a 500ms alpha window, that leaves ~264ms for signal. We target <100ms to give buffer. On a 200ms window, this is too tight — only larger divergences are tradeable. |
| **Model inference timeout** | <30ms | Within the 100ms budget: ~10ms for event receipt, ~30ms for inference, ~10ms for ensemble + delivery, ~50ms buffer. |
| **Signal delivery method** | WebSocket or gRPC push | REST polling is too slow. LYS pushes divergence events to us, we push signal back. No DB in the hot path. |
| **Signal uptime** | >99.5% | LYS needs to trust the signal is always available. Downtime = missed trades = lost revenue = lost PnL share. |
| **Model coverage** | 10+ liquid pairs | The Chainlink gate requires positive p99 latency differential across 10+ pairs. Models must cover the same universe. |
| **Minimum signal accuracy** | >55% on corrections >10bps | Below this, signal doesn't beat the on-chain-only baseline. Hard floor from the brief. |

**Architecture implication**: The competition node runs continuously, scoring models on live data. A separate production node runs alongside it with the top-performing models, serving live signals to LYS Flash. The latency SLAs apply to the production node. The competition node can be slightly more relaxed on latency since its job is ranking, not execution. Some initial backtesting validates the pipeline, but the competition is primarily live.

### Competition 2: Meteora LP Behavior Classifier

The alpha window is 30–180 seconds, but front-loaded — the first 5–10s after an LP shift is where the bulk of the edge lives (before other participants react). The standard coordinator node pipeline works here.

| SLA | Target | Rationale |
|---|---|---|
| **End-to-end signal latency** (LP shift occurs → signal delivered to LYS) | <5s | 30–180s alpha window, but front-loaded. 5s captures the bulk of the edge. Achievable with MongoDB change streams (sub-second detection) + fast inference. |
| **Feed polling / change stream latency** | <2s | The bottleneck. MongoDB change streams on replica set give sub-second. Polling mode must be ≤2s intervals. |
| **Model inference timeout** | <1s | Hard timeout. Models that exceed this are killed. Keeps end-to-end within 5s budget. |
| **Signal delivery method** | REST or WebSocket | 5s budget is generous enough for REST polling by LYS. WebSocket preferred for lower latency. |
| **Signal uptime** | >99% | Less critical than T1 (lower revenue per signal, longer alpha windows). |
| **Minimum signal accuracy** | >60% directional on LP shifts >$100k | The brief's own threshold. Below this, signal doesn't cover transaction costs and slippage. |
| **Ground truth resolution** | 30–180s after prediction | Raydium price change measured from prediction timestamp. Score worker fetches Raydium events from MongoDB. |

---

## 3. Model Interface

### Competition 1: CEX-DEX Correction Predictor

#### Inference Input

Models receive a snapshot of the current divergence event. **Features are anonymized in Stage 1** (labeled `gordon_Feature_1` etc. per the brief) to protect data provenance and prevent participants from front-running specific tokens independently. In Stage 2, top models get named features.

**Data obfuscation strategy** (from LowFreq PDF):
- Token identifiers stripped — assets referenced by anonymous IDs, not mint addresses
- Time-shifted windows — historical data chunks offset by variable delays
- Feature normalization — absolute values converted to z-scores or percentile ranks

```python
class InferenceInput(BaseModel):
    """Anonymized in Stage 1. Named features in Stage 2."""
    subject: str = ""              # anonymous pair ID (not mint address)
    # CEX-DEX divergence features
    cex_price: float = 0.0         # latest Chainlink CEX price (or z-score in Stage 1)
    dex_price: float = 0.0         # current DEX pool price
    divergence_bps: float = 0.0    # CEX-DEX spread in basis points
    cex_velocity: float = 0.0      # rate of CEX price change (bps/s)
    volume_spike: float = 0.0      # recent volume vs baseline (ratio)
    pool_depth_bid: float = 0.0    # liquidity available on bid side
    pool_depth_ask: float = 0.0    # liquidity available on ask side
    spread_bps: float = 0.0        # DEX bid-ask spread
    regime: str = ""               # volatility regime label
    timestamp: int = 0             # event timestamp (unix ms)
```

**Note**: The LowFreq PDF describes Enigma's 50+ features across 10 categories (price metrics, volume, holder dynamics, dev activity, swap events, graduation events, technical indicators, P&L conditions). For Competition 1, we use a subset focused on CEX-DEX divergence. The full Enigma feature set is more relevant to a potential Competition 3 (graduated token alpha). See Question 15.

#### Inference Output — Strategy Options

The LowFreq PDF already scoped three submission pathways (mapped here alongside our expanded options):

| Strategy | Output shape | LowFreq PDF mapping | Pros | Cons |
|---|---|---|---|---|
| **A) Probabilistic Modeling** | `{distribution: GaussianMixture}` or `{mean: float, std: float, skew: float}` | **Probabilistic Modeling** ★ | Maximum information — full return distribution enables optimal position sizing. Meta-model aggregates distributions naturally. LowFreq PDF recommends as Phase 1 alongside B. Most ML-native format (CrunchDAO's 11k engineers know this). | Complex submission format. Proper scoring rules required (CRPS, log score). Most crypto quants unfamiliar with distribution submission. Potentially overkill for binary correction events (>10bps or not). |
| **B) Directional probability** | `{correction_prob: float, direction: "up"\|"down"}` | **Directional Probability** ★ | Best information density for a binary event. Calibration-based scoring (Brier/log loss) is hard to overfit. LYS sets their own confidence threshold. Ensembles naturally via probability averaging. LowFreq PDF recommends as Phase 1 alongside A. | Participants need to understand calibration. Thinner pool than simpler formats. Need to define what "threshold" means (>10bps per brief). |
| **C) Binary direction** | `{direction: "up"\|"down"}` | Directional Probability (simplified) | Simplest. Maximum participant accessibility. Easy to ensemble (majority vote). | 50% baseline makes small edges indistinguishable from noise. No sizing info for LYS. No confidence signal — can't threshold. |
| **D) Magnitude + direction** | `{value: float}` (signed bps) | — | Natural for quants. Clean single number. Easy to ensemble (weighted average). | At 200–800ms, magnitude prediction is noise. Models that predict ~0 every time score "well" on MSE. Converges toward F anyway. |
| **E) Time-to-move** | `{seconds_to_correct: float, direction: "up"\|"down"}` | — | Useful for execution timing. | Niche. Survival analysis scoring is unfamiliar. Hard to ensemble. |
| **F) Full price path** | `{path: [float, ...]}` | — | Maximum theoretical information. | Ensembling paths is noisy. Scoring is ambiguous. Very few good submissions at this timescale. Overkill. |
| **G) Risk-adjusted EV** | `{ev_bps: float}` (signed) | — | Combines direction, magnitude, confidence into one tradeable number. Quant-native. Clean to ensemble. | At sub-second horizons, EV estimate is dominated by noise. Needs IC-based scoring to avoid gaming. |
| **H) Opportunity filter** | `{trade: bool, direction: "up"\|"down"}` | — | Directly answers LYS's question: "should I trade this?" Filters noise. Precision is hard to fake. Low barrier but hard to game. | Binary output loses sizing/confidence info. Needs F1-style scoring to prevent abstention gaming. |
| **I) Regime classification** | `{regime: str}` | — | Most decay-resistant. LYS maps regimes to strategies. | Indirect. LYS must build regime→strategy mapping. Wrong taxonomy = useless. |
| **J) Order-level** | `{orders: [{side, size, price}]}` | **Order Level** (Phase 2) | Full execution control. Maximum signal richness. | Requires full ~236ms pipeline per tick. Critical latency. Phase 2 only. Not suitable for crowd-sourced competition at scale. |

**Recommendation: B (Directional probability) for Competition 1. Consider A (Probabilistic Modeling) for Competition 3.**

The three-track plan explicitly frames CrunchDAO's role as: "build a model to predict which liquid pairs will experience a price correction in the next 200-800 milliseconds, and with what magnitude." The minimum threshold is ">55% on corrections above 10 basis points." This is a binary event (correction >10bps or not) with a probability — which is exactly strategy B. It aligns with the LowFreq PDF's "Directional Probability" pathway, co-recommended as Phase 1.

Strategy A (Probabilistic Modeling — submitting full return distributions) is the LowFreq PDF's other Phase 1 recommendation and the more ML-native format. It's higher-information but the CEX-DEX correction problem is fundamentally binary: did the pool correct >10bps within 800ms, or not? A full distribution is more suited to Competition 3 (graduated token alpha) where the return profile is richer and the time horizon is longer.

```python
# Strategy B — recommended for Competition 1
class InferenceOutput(BaseModel):
    correction_prob: float = 0.0   # probability of correction >10bps within 800ms
    direction: str = "hold"        # "up", "down", or "hold"
    magnitude_bps: float = 0.0     # expected correction size (optional, for sizing)
```

### Competition 2: Meteora LP Behavior Classifier

#### Inference Input

Models receive a description of the LP position change event, enriched with pool context. The primary signal features come from LYS Core's decoded Meteora state. Historical context comes from MongoDB.

```python
class InferenceInput(BaseModel):
    subject: str = ""                    # pool/pair identifier
    # LP position change features
    bin_shift_direction: str = ""        # "up", "down", "symmetric"
    bin_range_vs_current: float = 0.0    # new bin range relative to current price (%)
    position_size_usd: float = 0.0       # capital size of the LP change ($)
    position_action: str = ""            # "add", "remove", "shift"
    # Pool context
    pool_tvl_usd: float = 0.0           # total value locked in pool
    current_price: float = 0.0          # current pool price
    recent_volume_usd: float = 0.0      # recent trading volume
    liquidity_concentration: float = 0.0 # how concentrated liquidity is around current price
    # Wallet context
    wallet_historical_accuracy: float = 0.0  # this wallet's past signal accuracy (if trackable)
    # Market context
    volatility_regime: str = ""          # current volatility regime
    time_of_day_utc: int = 0             # hour of day (regime feature)
```

#### Inference Output — Strategy Options

The brief specifies a "supervised classification model" with target variable "dominant price direction." The $100k qualifying filter is applied upstream at the feed level — models only see qualifying LP shifts.

| Strategy | Output shape | Source | Pros | Cons |
|---|---|---|---|---|
| **A) Binary direction** | `{direction: "up"\|"down"}` | **Three-track plan** ★ | What the brief asks for. Simplest. Highest participant count. Easy to score (accuracy). Easy to ensemble (majority vote). $100k filter applied upstream, not by the model. | No confidence signal — can't threshold by conviction. No sizing info. A 2bps and 50bps correct prediction score the same. |
| **B) Directional probability** | `{direction: "up"\|"down", confidence: float}` | **Our recommendation** ★ | Superset of A — adds calibrated confidence without changing the core task. LYS can threshold by confidence (only trade when confidence >X). Ensembles via probability averaging (better than majority vote). Calibration is hard to fake. Marginal added complexity for participants. | Participants need to output meaningful confidence, not just 1.0 every time. Scoring must account for calibration (Brier) alongside accuracy. |
| **C) Magnitude + direction** | `{value: float}` (signed bps) | — | Sizing info for LYS. Natural for quants. | 30–180s magnitude prediction is mostly noise. MSE rewards hedging toward zero. |
| **D) Opportunity filter** | `{trade: bool, direction: "up"\|"down"}` | — | Model decides which LP shifts to trade on. Filters noise. | Brief already filters at $100k upstream — adding a second filter layer is redundant complexity. |

**Recommendation: B (Directional probability) — the brief's binary direction (A) plus a confidence field.**

The brief asks for A. We propose B as a minimal upgrade: same core task (classify direction on pre-filtered $100k+ LP shifts), but with a `confidence` float that gives LYS a threshold knob and gives us better ensemble behavior. Participants who don't care about calibration can output 1.0 and it reduces to A. But participants who calibrate well get rewarded, and the ensemble signal improves.

```python
# Strategy B — recommended for Competition 2
# Superset of the brief's binary direction (A) — adds confidence
class InferenceOutput(BaseModel):
    direction: str = "hold"        # "up" or "down" (the brief's target variable)
    confidence: float = 0.5        # 0.0–1.0, how confident in this direction
```

---

## 4. Post-Processing & Ensembling

### Option A: Raw top-model signal (no ensemble)

Use the single best-performing model's output directly.

| Pros | Cons |
|---|---|
| Simplest. Zero latency overhead. | Single point of failure. One model's bad day = bad signal. No diversity benefit. Alpha decay hits hard — when the best model decays, everything decays. |

### Option B: Majority vote ensemble (for binary/direction outputs)

Aggregate top-N models by majority vote on direction. Trade if ≥K models agree.

| Pros | Cons |
|---|---|
| Simple. Naturally filters noise — consensus = higher conviction. Robust to individual model failures. | Loses confidence/magnitude information. All models weighted equally regardless of track record. Slow models drag down ensemble latency. |

### Option C: PnL-weighted probability averaging

Average top-N models' probability/confidence outputs, weighted by each model's recent realized PnL contribution.

| Pros | Cons |
|---|---|
| Best-performing models get most influence. Naturally adapts as models decay. Smooth probability output — LYS can threshold flexibly. Directly aligned with revenue (PnL-weighted = revenue-weighted). | Requires PnL attribution per model (need LYS execution feedback). Weighting lag — takes time to accumulate enough PnL signal. New models start with zero weight. |

### Option D: Regime-conditional ensemble

Different ensemble weights per market regime (e.g., high-vol uses models that perform well in volatility, low-vol uses different set).

| Pros | Cons |
|---|---|
| Best theoretical performance — right models for right conditions. Handles regime shifts where single ensembles fail. | Complex. Requires regime detection (itself a model). Small sample sizes per regime make weight estimation noisy. Overfitting risk on regime boundaries. |

### Option E: Stacked ensemble (meta-model)

A meta-model that takes all base model outputs as features and produces the final signal. The LowFreq PDF explicitly describes this: "An ensemble meta-model aggregating hundreds of probabilistic submissions."

| Pros | Cons |
|---|---|
| Can learn non-linear combinations. Handles correlated models. Theoretically optimal. LYS Flash's 100% reliability makes this operationally viable. | Overfitting risk — meta-model trains on a small number of base model outputs. Adds inference latency (two-stage). Black box — harder to debug when signal is wrong. |

**Recommendation: Start with Option B (majority vote) for Competition 2, graduate to Option C (PnL-weighted averaging) for both competitions once live PnL data is available.**

Rationale: Majority vote is simple, robust, and works with the H (opportunity filter) model output. It's a natural fit for Stage 1. Once models are running live and we have PnL attribution from LYS, PnL-weighted averaging is the correct long-term approach — it directly optimizes for revenue, which is what we're paid on. The brief explicitly calls for "models weighted by live PnL contribution."

For Competition 1 (sub-100ms), ensemble must be pre-computed or extremely fast. PnL-weighted averaging of probabilities adds negligible compute.

The LowFreq PDF's meta-model vision (Option E) is the long-term goal but requires Phase 2 maturity — enough base models with enough track record to train a meaningful meta-model.

**Post-processing steps (both competitions):**

1. **Minimum confidence threshold** — filter out signals below a floor (prevents noise trades)
2. **Transaction cost filter** — reject signals where expected magnitude < estimated execution cost
3. **Correlation filter** — if multiple pairs fire simultaneously, check for common-cause (market-wide move vs pair-specific)
4. **Rate limiting** — cap maximum signal frequency per pair to prevent overtrading

---

## 5. Scoring & Metrics

### Competition 1: CEX-DEX Correction Predictor

**Primary scoring metric**: Brier score on correction probability.

For each prediction where the model outputs `correction_prob` and `direction`:
- **Ground truth**: Did a correction >10bps occur within 800ms? What direction?
- Label = 1 if correction happened in predicted direction, 0 otherwise
- Brier score = mean((prob - label)²) → lower is better

**Secondary metrics** (tracked, contribute to ranking):

| Metric | What it measures | Why it matters |
|---|---|---|
| **Hit rate** | % of predictions where direction was correct | Raw accuracy — the brief's "55% threshold" |
| **Profit factor** | gross winning bps / gross losing bps | Directly maps to trading profitability |
| **Calibration error** | |predicted prob - actual frequency| per probability bin | Model knows what it knows. Overconfident models are dangerous. |
| **Coverage** | % of qualifying divergence events where model produced a signal | Models that only predict the easy ones aren't useful at scale |
| **IC (Information Coefficient)** | Rank correlation between predicted magnitude and actual magnitude | Is the magnitude estimate actually informative? |
| **Pair-level accuracy** | Hit rate broken down by trading pair | Catches models that only work on SOL/USDC but fail elsewhere |

**Scoring function signature:**
```python
def score(prediction: dict, ground_truth: dict) -> dict:
    # prediction: {correction_prob, direction, magnitude_bps}
    # ground_truth: {corrected: bool, actual_direction, actual_magnitude_bps, entry_price, exit_price}
    return {
        "value": brier_score,        # primary ranking metric (lower = better)
        "hit_rate": ...,
        "profit_factor": ...,
        "calibration_error": ...,
        "coverage": ...,
        "success": True,
        "failed_reason": None,
    }
```

**Note on ranking direction**: Brier score is "lower is better." Set `aggregation.ranking_direction = "asc"` in CrunchConfig. Alternatively, use `1.0 - brier_score` as the value field so higher = better (more intuitive for leaderboard).

### Competition 2: Meteora LP Behavior Classifier

**Primary scoring metric**: Profit factor on `trade=True` predictions.

For each prediction where `trade=True`:
- **Ground truth**: Raydium price direction and magnitude over 30–180s after the LP shift
- If `direction` matches actual direction → winning trade (actual_magnitude_bps)
- If `direction` is wrong → losing trade (-actual_magnitude_bps, or a fixed cost penalty)
- Profit factor = sum(winning bps) / sum(|losing bps|)

**Secondary metrics:**

| Metric | What it measures | Why it matters |
|---|---|---|
| **Directional accuracy** | % correct direction on `trade=True` predictions | The brief's "60% threshold." Core metric. |
| **Precision** | % of `trade=True` signals that were profitable | Are you trading the right events? |
| **Recall** | % of actually profitable LP shifts where model said `trade=True` | Are you missing opportunities? |
| **F1** | Harmonic mean of precision and recall | Prevents abstention gaming (never trading) or spam (always trading) |
| **Average profit per trade** | Mean bps earned on `trade=True` signals | Revenue proxy |
| **Trade frequency** | Number of `trade=True` signals per day | Too few = not useful at scale. Too many = likely noise. |
| **Max drawdown** | Worst peak-to-trough PnL decline | Risk measure — avoids models that are profitable on average but blow up |

**Scoring function signature:**
```python
def score(prediction: dict, ground_truth: dict) -> dict:
    # prediction: {trade, direction, confidence}
    # ground_truth: {actual_direction, actual_magnitude_bps, raydium_price_start, raydium_price_end}
    if not prediction["trade"]:
        # Model chose not to trade — score as neutral
        return {"value": 0.0, "trade_taken": False, "success": True, "failed_reason": None}

    correct = prediction["direction"] == ground_truth["actual_direction"]
    pnl_bps = ground_truth["actual_magnitude_bps"] if correct else -ground_truth["actual_magnitude_bps"]

    return {
        "value": pnl_bps,               # primary: simulated PnL per trade
        "direction_correct": correct,
        "profit_bps": pnl_bps,
        "confidence": prediction.get("confidence", 0.0),
        "trade_taken": True,
        "success": True,
        "failed_reason": None,
    }
```

**Aggregation**: The `value` field is averaged over rolling windows (24h, 72h, 168h). Models ranked by `score_recent` (24h average PnL per trade). This means models that trade rarely but accurately can beat models that trade often but noisily — which is the correct incentive structure for a filtering problem.

**Handling `trade=False`**: Predictions where `trade=False` score 0.0. This means:
- A model that never trades gets 0.0 average — not penalized, but not rewarded
- A model that always trades gets average PnL — if negative, it ranks below the "never trade" model
- The optimal strategy is to trade only when you have real edge — exactly what we want

To prevent pure abstention (a model that says `trade=False` 100% of the time), enforce a **minimum trade frequency**: models must produce `trade=True` on at least 5% of qualifying LP shifts (>$100k) to be eligible for ranking.

---

## 6. Compensation — Distribution Design

Regardless of what revenue enters the system (trading PnL share, token allocations, sponsorship, capital ramp returns), the question is: **how do we distribute it to model writers in a way that maximizes signal quality and retains top talent?**

### Distribution Strategies

#### Strategy 1: Pure PnL-weighted

Each model's share = its contribution to ensemble PnL as a proportion of total ensemble PnL.

| Pros | Cons |
|---|---|
| Perfectly aligned with mission — we pay for what makes money. Best models get the most. Zero gaming potential (PnL is the ultimate metric). | Cold start problem — new models have zero PnL history and get nothing. Creates winner-takes-all dynamic that discourages new entrants. Single bad week can wipe a model's accumulated weight. Requires PnL attribution feedback from LYS (dependency). |

#### Strategy 2: Rank tiers (current coordinator node default)

Fixed percentage per rank bracket (e.g. 1st = 35%, 2nd–5th = 10% each, 6th–10th = 5% each, rest split equally).

| Pros | Cons |
|---|---|
| Simple. Predictable for participants. No external dependency (ranked by our own scoring). Encourages broad participation — even rank 10 gets meaningful payout. | Not aligned with actual value creation. A model ranked #1 by Brier score might contribute less ensemble PnL than #3. Rewards accuracy ranking, not revenue contribution. |

#### Strategy 3: Hybrid — tier floor + PnL bonus

Base payout by rank tier (guarantees income for good models). Bonus pool distributed by PnL contribution (rewards actual value creation).

| Pros | Cons |
|---|---|
| Best of both worlds. Tier floor solves cold start (new models that rank well get paid immediately). PnL bonus rewards long-term value. Retains top talent AND attracts new entrants. | More complex to implement. Need to decide the split ratio (e.g. 60% tier / 40% PnL bonus). PnL bonus still depends on attribution feedback. |

#### Strategy 4: Tournament-style with promotion/relegation

Fixed pool split equally among models in the "live ensemble." Models promoted into the ensemble by competition ranking. Models relegated out when they underperform for N consecutive weeks.

| Pros | Cons |
|---|---|
| Strong retention incentive — stay in the ensemble to keep earning. Clear meritocracy. Equal pay within the ensemble means collaboration over competition among live models. | Binary (in or out) may frustrate borderline models. Equal pay doesn't reward the best model in the ensemble. Relegation rules need careful design to avoid volatility. |

#### Strategy 5: Market-maker model

Models "stake" on their own predictions. Correct predictions earn from a pool funded by incorrect predictions. Self-regulating — confident models risk more, earn more.

| Pros | Cons |
|---|---|
| Self-selecting for conviction. No need for external PnL attribution. Models internalize risk. | Requires participants to put capital at risk — dramatically reduces participation. Regulatory complexity. Not how CrunchDAO competitions typically work. |

**Recommendation: Strategy 3 (hybrid tier floor + PnL bonus).**

It solves the two competing goals simultaneously:
- **Attract and retain**: Tier floor guarantees meaningful compensation for top-ranked models from day one, even before live PnL data exists. New models can enter, rank well, and earn immediately.
- **Optimize for revenue**: PnL bonus ensures the models that actually generate money get disproportionately rewarded over time. This is what the briefs call for ("weighted by live PnL contribution").
- **Phased rollout**: Launch with pure tier distribution (Strategy 2) in Stage 1 when there's no live PnL data. Add PnL bonus in Stage 2 when LYS feedback loop is live. Gradually shift the ratio toward PnL as the system matures (e.g. 80/20 tier/PnL → 50/50 → 30/70).

### Anti-Alpha-Decay Incentive Design

The brief identifies alpha decay as a core risk — models that work today will stop working as LPs adapt. The distribution design must incentivize continuous model improvement, not just initial submission.

| Mechanism | How it works | Effect |
|---|---|---|
| **Rolling windows for ranking** | Rank by 24h/72h/168h score averages, not all-time. | Decaying models drop in rank. Fresh models rise. Forces continuous improvement. |
| **New model bonus period** | New submissions get a 2-week evaluation window with guaranteed minimum payout if they rank in top 50%. | Reduces cold-start penalty. Encourages experimentation. |
| **Quarterly rotation pressure** | Bottom N% of ensemble models are dropped each quarter. Open slots filled by top competition performers. | Prevents stale models from collecting rent. Creates ongoing entry opportunity. |
| **Diversity bonus** | Models whose predictions are uncorrelated with the ensemble get a small ranking boost. | Prevents convergence to a single strategy. Ensemble diversity = better signal. Directly combats alpha decay. |

### Coordinator Node Implementation

Both competitions use the same emission pipeline:
1. Score worker aggregates scores → snapshots → leaderboard
2. Leaderboard ranked by primary metric (Brier score or profit factor)
3. `build_emission()` converts rankings into `EmissionCheckpoint` with `CruncherReward` per model
4. Checkpoint submitted on-chain → claimable by participants

For the hybrid model (Strategy 3), the `build_emission()` override splits the reward pool:

```python
def hybrid_emission(ranked_entries, crunch_pubkey, tier_pct=0.6, **kwargs):
    """Split rewards: tier_pct by rank, remainder by PnL contribution."""
    tier_rewards = compute_tier_rewards(ranked_entries, weight=tier_pct)
    pnl_rewards = compute_pnl_rewards(ranked_entries, weight=1.0 - tier_pct)

    combined = []
    for i, entry in enumerate(ranked_entries):
        total_pct = tier_rewards[i] + pnl_rewards[i]
        combined.append(CruncherReward(
            cruncher_index=i,
            reward_pct=pct_to_frac64(total_pct),
        ))
    return EmissionCheckpoint(crunch=crunch_pubkey, cruncher_rewards=combined, ...)
```

The `tier_pct` parameter controls the ratio and can be adjusted over time as live PnL data matures:
- **Stage 1 (Rally)**: `tier_pct=1.0` — pure rank tiers, no PnL data exists yet
- **Stage 2 (early)**: `tier_pct=0.6` — 60% rank, 40% PnL bonus
- **Stage 2 (mature)**: `tier_pct=0.3` — shift toward PnL as attribution stabilizes

---

## 7. Questions for LYS Labs

### Data & Infrastructure

1. **LYS Core real-time API for LP position changes**: The MongoDB collections contain swap events (`meteora_dlmm_events`). LP position changes (add/remove/shift liquidity) are the actual signal per the brief. Does LYS Core's real-time API expose LP position change events specifically, or do we need to derive them from raw `spl_token_events` (3.9B/day)?

2. **Latency from on-chain event to MongoDB insert**: For Competition 2's <5s SLA, the bottleneck is how fast LYS's pipeline writes events to MongoDB after they occur on-chain. What is the typical and p99 latency of this pipeline?

3. **Chainlink Data Streams access for Competition 1**: Do we get direct API access to Chainlink Data Streams, or does LYS proxy the data? For the Stage 1 Rally dataset, we need historical CEX price feeds aligned with DEX pool state. Who builds this labeled dataset?

4. **Feature anonymization for Stage 1 Rally**: The brief says features are labeled `gordon_Feature_1` etc. Does LYS provide the anonymized dataset, or does CrunchDAO build it from raw data and anonymize? Who controls the feature engineering?

5. **Wallet behavior data from Enigma**: The brief lists "historical wallet behavior patterns from Enigma" as a Competition 2 input feature. What data does Enigma expose? Is this available via API, or is it a dataset we receive?

### Signal Delivery

6. **Signal consumption API**: What format does LYS Flash consume signals in? REST/WebSocket/gRPC? What's the payload schema? Do they poll us, or do we push to them?

7. **PnL attribution feedback loop**: For PnL-weighted model compensation, we need LYS to report back which signals were executed and what PnL resulted. What does this feedback loop look like? Real-time? Daily batch? On-chain?

8. **Confidence threshold**: Who sets the minimum confidence threshold for trading — CrunchDAO (in the ensemble) or LYS (in execution)? If LYS, what's the initial threshold?

### Competition Design

9. **Stage 1 Rally timing**: The brief says Weeks 1–4. Is that 1–4 from contract signing, or from when the dataset is ready? CrunchDAO needs ~2 weeks to scaffold the competition infrastructure once the dataset exists.

10. **Pair universe for Competition 2**: The brief says "liquid CEX-correlated pairs" for Track 1, but Track 3 is Meteora→Raydium. Which specific Meteora DLMM pools should Competition 2 cover? All pools with >$500k TVL? Only pools where there's a corresponding Raydium pair?

11. **Minimum LP shift size**: The brief says "$100k+ LP moves." Is that $100k in the LP position change itself, or $100k in the pool that the change happened in? A $100k shift in a $500k pool is very different from a $100k shift in a $50M pool.

### Commercial

12. **Revenue to CrunchDAO**: What is the total revenue structure flowing to CrunchDAO? We need to know the pool size to design distribution ratios that attract and retain top talent.

13. **PnL attribution feedback**: For PnL-weighted distribution (Strategy 3 bonus pool), we need per-model PnL attribution from LYS. What does this feedback loop look like? Real-time? Daily batch? On-chain? This determines when we can graduate from pure tier ranking to hybrid distribution.

14. **Exclusivity**: Is CrunchDAO the exclusive signal provider, or could LYS add other signal sources? If not exclusive, how is PnL attributed between signal sources?

### Reconciling the Two Proposals

15. **LowFreq Alpha Engine vs Three-Track Plan**: The LowFreq PDF describes a broader alpha discovery platform on graduated Raydium tokens using Enigma's 50+ behavioral features. The three-track plan describes specific CEX-DEX and Meteora LP signals. Are these:
    - (a) The same initiative at different stages of scoping?
    - (b) Separate competitions that will run in parallel?
    - (c) The LowFreq PDF is superseded by the three-track plan?
    This affects how many competitions we build, the asset universe, and the feature space.

16. **Revenue model**: The three-track plan implies PnL share on trading revenue. The LowFreq PDF describes a three-layer model (competition fees + execution fees + capital ramp). Which is the actual commercial structure? Are these complementary (three layers + PnL share) or alternative models?

17. **Capital ramp vs pooled capital**: The LowFreq PDF proposes $5k→$10k→$30k+ capital allocation per top model with isolated TEE wallets. The three-track plan describes Auros providing $5–15M pooled capital. How do these interact? Does the capital ramp come from the pooled capital?

18. **Asset universe**: The three-track plan targets "15–25 liquid CEX-correlated pairs with ≥$500k TVL." The LowFreq PDF recommends "graduated tokens on Raydium." These are very different universes. Which applies to which competition? Could Competition 1 run on liquid pairs while a separate Competition 3 runs on graduated tokens?

19. **Community Market Maker**: The LowFreq PDF mentions a stretch goal — an ensemble meta-model providing non-adversarial liquidity. Is this still on the roadmap? If so, it's a third/fourth competition type with very different model interface and scoring.
