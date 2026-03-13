# Benchmark CI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run all three benchmarks (standard, tournament, trading) daily in GitHub Actions with change detection, configurable model, and Slack failure notifications.

**Architecture:** Single workflow file with sequential benchmark steps, each using `continue-on-error`. A change-detection step skips scheduled runs when main hasn't changed. A Slack notification step fires only on failure.

**Tech Stack:** GitHub Actions, Claude Code CLI (`npm install -g @anthropic-ai/claude-code`), Docker Compose (pre-installed on ubuntu-latest), `curl` for Slack webhook.

---

### Task 1: Create the benchmark workflow file

**Files:**
- Create: `.github/workflows/benchmark.yml`

**Step 1: Write the workflow file**

```yaml
name: Benchmarks

on:
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:
    inputs:
      model:
        description: 'Claude model to use'
        default: 'sonnet'
        type: choice
        options:
          - haiku
          - sonnet
          - opus
      benchmarks:
        description: 'Which benchmarks to run'
        default: 'all'
        type: choice
        options:
          - all
          - standard
          - tournament
          - trading

env:
  AGENT_CMD: claude
  BENCHMARK_EVIDENCE: fast
  BENCHMARK_TIMEOUT: '900'

jobs:
  benchmark:
    runs-on: ubuntu-latest
    timeout-minutes: 90

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      # ── Change detection ──────────────────────────────────────────
      - name: Check for changes since last benchmark
        id: changes
        if: github.event_name == 'schedule'
        run: |
          LAST_SHA=""
          if [ -f /tmp/last-benchmarked-sha/sha.txt ]; then
            LAST_SHA=$(cat /tmp/last-benchmarked-sha/sha.txt)
          fi
          CURRENT_SHA=$(git rev-parse HEAD)
          echo "current_sha=$CURRENT_SHA" >> "$GITHUB_OUTPUT"
          if [ "$LAST_SHA" = "$CURRENT_SHA" ]; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
            echo "No changes since last benchmark ($CURRENT_SHA), skipping."
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
            echo "Changes detected: ${LAST_SHA:-none} -> $CURRENT_SHA"
          fi

      - name: Download last benchmarked SHA
        if: github.event_name == 'schedule'
        uses: dawidd6/action-download-artifact@v6
        with:
          name: last-benchmarked-sha
          path: /tmp/last-benchmarked-sha
          if_no_artifact_found: warn
          workflow: benchmark.yml
          search_artifacts: true

      - name: Re-check after download
        id: recheck
        if: github.event_name == 'schedule'
        run: |
          LAST_SHA=""
          if [ -f /tmp/last-benchmarked-sha/sha.txt ]; then
            LAST_SHA=$(cat /tmp/last-benchmarked-sha/sha.txt)
          fi
          CURRENT_SHA=$(git rev-parse HEAD)
          if [ "$LAST_SHA" = "$CURRENT_SHA" ]; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
            echo "No changes since last benchmark ($CURRENT_SHA), skipping."
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
            echo "Changes detected: ${LAST_SHA:-none} -> $CURRENT_SHA"
          fi

      # ── Setup ─────────────────────────────────────────────────────
      - name: Install uv
        if: steps.recheck.outputs.skip != 'true'
        uses: astral-sh/setup-uv@v5

      - name: Set up Python
        if: steps.recheck.outputs.skip != 'true'
        run: uv python install

      - name: Install dependencies
        if: steps.recheck.outputs.skip != 'true'
        run: uv sync --all-extras

      - name: Install Claude Code CLI
        if: steps.recheck.outputs.skip != 'true'
        run: npm install -g @anthropic-ai/claude-code

      - name: Resolve model
        id: config
        if: steps.recheck.outputs.skip != 'true'
        run: |
          MODEL="${{ github.event.inputs.model || 'sonnet' }}"
          BENCHMARKS="${{ github.event.inputs.benchmarks || 'all' }}"
          echo "model=$MODEL" >> "$GITHUB_OUTPUT"
          echo "benchmarks=$BENCHMARKS" >> "$GITHUB_OUTPUT"
          echo "Model: $MODEL, Benchmarks: $BENCHMARKS"

      # ── Benchmarks ────────────────────────────────────────────────
      - name: Run standard benchmark
        id: bench_standard
        if: steps.recheck.outputs.skip != 'true' && (steps.config.outputs.benchmarks == 'all' || steps.config.outputs.benchmarks == 'standard')
        continue-on-error: true
        timeout-minutes: 25
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          uv run python -m tests.benchmark.run_benchmark \
            --agent-cmd "claude --model ${{ steps.config.outputs.model }}" \
            --timeout ${{ env.BENCHMARK_TIMEOUT }} \
            --evidence ${{ env.BENCHMARK_EVIDENCE }}

      - name: Run tournament benchmark
        id: bench_tournament
        if: steps.recheck.outputs.skip != 'true' && (steps.config.outputs.benchmarks == 'all' || steps.config.outputs.benchmarks == 'tournament')
        continue-on-error: true
        timeout-minutes: 25
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          uv run python -m tests.benchmark_tournament.run_benchmark \
            --agent-cmd "claude --model ${{ steps.config.outputs.model }}" \
            --timeout ${{ env.BENCHMARK_TIMEOUT }}

      - name: Run trading benchmark
        id: bench_trading
        if: steps.recheck.outputs.skip != 'true' && (steps.config.outputs.benchmarks == 'all' || steps.config.outputs.benchmarks == 'trading')
        continue-on-error: true
        timeout-minutes: 25
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          uv run python -m tests.benchmark_trading.run_benchmark \
            --agent-cmd "claude --model ${{ steps.config.outputs.model }}" \
            --timeout ${{ env.BENCHMARK_TIMEOUT }}

      # ── Artifacts ─────────────────────────────────────────────────
      - name: Upload benchmark results
        if: steps.recheck.outputs.skip != 'true' && !cancelled()
        uses: actions/upload-artifact@v4
        with:
          name: benchmark-results-${{ github.run_number }}
          retention-days: 90
          path: |
            tests/benchmark/results/*.json
            tests/benchmark/logs/*.log
            tests/benchmark_tournament/results/*.json
            tests/benchmark_tournament/logs/*.log
            tests/benchmark_trading/results/*.json
            tests/benchmark_trading/logs/*.log

      - name: Save benchmarked SHA
        if: steps.recheck.outputs.skip != 'true' && !cancelled()
        run: |
          mkdir -p /tmp/benchmarked-sha
          git rev-parse HEAD > /tmp/benchmarked-sha/sha.txt

      - name: Upload benchmarked SHA
        if: steps.recheck.outputs.skip != 'true' && !cancelled()
        uses: actions/upload-artifact@v4
        with:
          name: last-benchmarked-sha
          path: /tmp/benchmarked-sha/sha.txt
          overwrite: true

      # ── Slack notification ────────────────────────────────────────
      - name: Notify Slack on failure
        if: steps.recheck.outputs.skip != 'true' && !cancelled() && (steps.bench_standard.outcome == 'failure' || steps.bench_tournament.outcome == 'failure' || steps.bench_trading.outcome == 'failure')
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
        run: |
          COMMIT_SHA=$(git rev-parse --short HEAD)
          COMMIT_MSG=$(git log -1 --format=%s)
          RUN_URL="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          MODEL="${{ steps.config.outputs.model }}"

          status_emoji() {
            case "$1" in
              success) echo "✅" ;;
              failure) echo "❌" ;;
              skipped) echo "⏭️" ;;
              *) echo "❓" ;;
            esac
          }

          STANDARD=$(status_emoji "${{ steps.bench_standard.outcome }}")
          TOURNAMENT=$(status_emoji "${{ steps.bench_tournament.outcome }}")
          TRADING=$(status_emoji "${{ steps.bench_trading.outcome }}")

          curl -s -X POST "$SLACK_WEBHOOK_URL" \
            -H 'Content-Type: application/json' \
            -d "$(cat <<EOF
          {
            "text": "Benchmark regression on main",
            "blocks": [
              {
                "type": "header",
                "text": {"type": "plain_text", "text": "⚠️ Benchmark Regression"}
              },
              {
                "type": "section",
                "fields": [
                  {"type": "mrkdwn", "text": "*Commit:*\n\`${COMMIT_SHA}\` ${COMMIT_MSG}"},
                  {"type": "mrkdwn", "text": "*Model:*\n${MODEL}"}
                ]
              },
              {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "${STANDARD} Standard  ${TOURNAMENT} Tournament  ${TRADING} Trading"}
              },
              {
                "type": "actions",
                "elements": [
                  {"type": "button", "text": {"type": "plain_text", "text": "View Run"}, "url": "${RUN_URL}"}
                ]
              }
            ]
          }
          EOF
          )"
```

**Step 2: Verify the YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/benchmark.yml'))"`
Expected: No error

**Step 3: Commit**

```bash
git add .github/workflows/benchmark.yml
git commit -m "Add daily benchmark CI workflow"
```

---

### Task 2: Add Makefile targets for CI-friendly invocation

**Files:**
- Modify: `Makefile:62-74`

**Step 1: Add `benchmark-all` and individual CI targets**

Add after the existing benchmark targets:

```makefile
benchmark-tournament:
	uv run python -m tests.benchmark_tournament.run_benchmark --agent-cmd "$(AGENT_CMD)" --timeout $(BENCHMARK_TIMEOUT)

benchmark-trading:
	uv run python -m tests.benchmark_trading.run_benchmark --agent-cmd "$(AGENT_CMD)" --timeout $(BENCHMARK_TIMEOUT)

benchmark-all: benchmark benchmark-tournament benchmark-trading
```

**Step 2: Verify targets work**

Run: `make -n benchmark-all AGENT_CMD=claude`
Expected: Prints the three commands without executing

**Step 3: Commit**

```bash
git add Makefile
git commit -m "Add benchmark-tournament, benchmark-trading, and benchmark-all Makefile targets"
```

---

### Task 3: Test workflow locally with act (optional, manual)

This task is a manual verification step — not automated.

**Step 1: Verify secrets are configured**

Go to the GitHub repo → Settings → Secrets and variables → Actions → Add:
- `ANTHROPIC_API_KEY`: Your API key from console.anthropic.com
- `SLACK_WEBHOOK_URL`: Your Slack incoming webhook URL

**Step 2: Trigger a manual run**

Go to Actions → Benchmarks → Run workflow → Select model and benchmarks → Run

**Step 3: Monitor the run**

Watch the Actions tab for the run to complete. Check:
- [ ] Claude CLI installs successfully
- [ ] Each benchmark step runs (or skips based on selection)
- [ ] Artifacts are uploaded
- [ ] Slack notification fires on failure

**Step 4: Verify change detection**

Trigger the workflow again manually — it should run (manual always runs).
Wait for the next scheduled run — it should skip (no new commits).

---

### Task 4: Document the benchmark CI setup

**Files:**
- Modify: `docs/architecture.md` (or appropriate doc)

**Step 1: Add a CI section**

Add a brief section to the existing docs explaining:
- How to trigger benchmarks manually
- What secrets are needed
- Where to find results (Actions → artifacts)
- How to change the model or select specific benchmarks

**Step 2: Commit**

```bash
git add docs/architecture.md
git commit -m "Document benchmark CI workflow"
```
