# Agent Policy Pack Design

**Date:** 2026-02-21
**Branch:** `feat/agent-policy-pack`
**Status:** Implemented

## Problem

The scaffold ships SKILL.md files at three levels (workspace, node, challenge) that get auto-discovered by pi as skills. This is wrong because:

1. They are project context, not reusable skills — they pollute the skill list
2. Every scaffolded workspace creates duplicate skills with similar names
3. Content mixes architecture facts, operational rules, and workflow protocols into single files
4. Policy can't be swapped without rewriting architecture docs

## Solution

Replace SKILL.md files with a structured `.agent/` directory. Separate content into three layers:

- **Layer A — Context** (read-only, factual): architecture, extension points, edit boundaries
- **Layer B — Policy** (rules): approval gates, allowed operations, output contracts
- **Layer C — Protocol** (workflows): playbooks for feature, bugfix, customize, release

The `crunch-coordinate` skill (separate repo) becomes the single entry point that tells agents to read `.agent/` files when working in a coordinator workspace.

## Structure

```
scaffold/
├── .agent/
│   ├── README.md              ← for humans: what this folder is
│   ├── context.md             ← Layer A: project architecture, extension points, do-not-edit zones
│   ├── policy.md              ← Layer B: rules, approval gates, output contracts
│   ├── approvals.yml          ← machine-readable subset of policy (parseable by tooling)
│   └── playbooks/
│       ├── feature.md         ← Layer C: add a feature
│       ├── bugfix.md          ← Layer C: fix a bug
│       ├── customize.md       ← Layer C: customize competition
│       └── release.md         ← Layer C: deploy to production
├── node/.agent/
│   └── context.md             ← node-specific: workers, docker, API, edit boundaries
└── challenge/.agent/
    └── context.md             ← challenge-specific: tracker, scoring, backtest
```

## Content migration

All content from the three SKILL.md files and the coordinator-node-starter installed skill was migrated:

| Source | Destination |
|---|---|
| `scaffold/SKILL.md` → fast path, logs, edit map, validation | `.agent/context.md`, `.agent/policy.md` |
| `scaffold/node/SKILL.md` → commands, workers, API, security, edit boundaries | `node/.agent/context.md` |
| `scaffold/challenge/SKILL.md` → files, backtest, guidance | `challenge/.agent/context.md` |
| `coordinator-node-starter` → architecture, CrunchConfig, scoring, ensembles, lifecycles, emission | `.agent/context.md` |
| `coordinator-node-starter` → scaffolding rules, design checklist, E2E workflow | `crunch-coordinate` skill |
| `coordinator-node-starter` → mandatory verification | `.agent/policy.md` |

### Dropped content

- SKILL.md frontmatter (name/description/summary) — skill metadata, not content
- Sub-skills section — referenced 4 SKILL.md files that never existed

## Changes to crunch-coordinate skill (separate repo)

- Description expanded to trigger when working inside coordinator workspaces
- Added scaffolding rules, design checklist, and deploy workflow (from coordinator-node-starter)
- Added "Working in a Coordinator Workspace" section directing agents to `.agent/` files

## Manual follow-up

- Remove `~/.pi/agent/skills/coordinator-node-starter/` — symlink to deprecated skill
- The packs overlay system works unchanged: packs can override any `.agent/` file

## Key properties

- **Token efficient**: agent reads only the files relevant to the current task
- **Pack-friendly**: `.agent/` participates in the existing packs overlay system
- **No skill pollution**: scaffolded workspaces contain no SKILL.md files
- **Machine-readable gates**: `approvals.yml` is parseable by tooling, not just LLM prompting
- **Separated concerns**: policy changes don't require rewriting architecture docs
