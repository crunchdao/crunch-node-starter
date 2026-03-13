# Benchmark CI/CD Design

Run all three benchmarks (standard, tournament, trading) daily in GitHub Actions when main has new commits, with manual trigger support and Slack notifications on failure.

## Decisions

- **Runner**: GitHub-hosted `ubuntu-latest` (move to self-hosted if needed later)
- **Agent**: Claude Code CLI with `ANTHROPIC_API_KEY` (subscription auth not viable for headless CI)
- **Model**: Configurable per run, default `sonnet`
- **Execution**: Sequential in a single job (`continue-on-error` per benchmark)
- **Results**: Uploaded as GitHub Actions artifacts (no committing to repo)
- **Notifications**: Slack webhook on failure/regression only
- **Cost**: ~$2-3/day for all three benchmarks with sonnet

## Triggers

```yaml
on:
  schedule:
    - cron: '0 2 * * *'       # Daily 2am UTC
  workflow_dispatch:
    inputs:
      model:
        description: 'Claude model'
        default: 'sonnet'
        type: choice
        options: [haiku, sonnet, opus]
      benchmarks:
        description: 'Which benchmarks'
        default: 'all'
        type: choice
        options: [all, standard, tournament, trading]
```

## Secrets

| Secret | Purpose |
|--------|---------|
| `ANTHROPIC_API_KEY` | Claude Code CLI authentication |
| `SLACK_WEBHOOK_URL` | Failure notifications |

## Change Detection

1. Download `last-benchmarked-sha` artifact from previous runs
2. Compare with current `HEAD` of main
3. Skip if unchanged (unless manual trigger)
4. Upload new SHA after successful run
5. First run / expired artifact → always run

## Job Structure

Single job, sequential steps:

```
Checkout → Setup (uv, Claude CLI) → Change Detection →
  Standard Benchmark (continue-on-error) →
  Tournament Benchmark (continue-on-error) →
  Trading Benchmark (continue-on-error) →
  Upload Artifacts → Slack (on failure only)
```

### Setup

1. `actions/checkout@v4`
2. `astral-sh/setup-uv@v5` + `uv python install` + `uv sync --all-extras`
3. `npm install -g @anthropic-ai/claude-code`
4. Docker Compose (pre-installed on ubuntu-latest)

### Benchmark Steps

Each benchmark:
- `AGENT_CMD=claude`
- `BENCHMARK_EVIDENCE=fast`
- `--model` flag from workflow input (default sonnet)
- Step timeout: 20 minutes
- `continue-on-error: true` so failures don't block subsequent benchmarks

### Artifacts

Single artifact containing:
- `tests/benchmark/results/*.json`
- `tests/benchmark_tournament/results/*.json`
- `tests/benchmark_trading/results/*.json`
- `tests/benchmark*/logs/*.log` (for debugging)

### Slack Notification

Fires when any benchmark step failed. Message includes:
- Which benchmark(s) failed with milestone counts
- Model used, commit SHA
- Link to the Actions run
- Implemented as `curl` to webhook URL
