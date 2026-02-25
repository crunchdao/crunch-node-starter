# .agent — Project Context for AI Agents

This folder contains structured context, policy, and workflow playbooks for AI agents working in this repository. These are **not** pi skills — they are plain markdown files read on demand.

## Structure

```
.agent/
├── README.md            ← you are here
├── context.md           ← project architecture, data flows, extension points
├── policy.md            ← rules, approval gates, output contracts
├── approvals.yml        ← machine-readable approval gates
└── playbooks/
    ├── feature.md       ← add a new feature
    ├── bugfix.md        ← diagnose and fix a bug
    ├── customize.md     ← customize competition types, scoring, feeds
    └── release.md       ← deploy to production
```

Subfolder-specific context lives in `node/.agent/context.md` and `challenge/.agent/context.md`.

## How agents use these files

1. Read `context.md` to understand the project
2. Read `policy.md` for rules and constraints
3. Read the relevant playbook for the task at hand
4. Read subfolder context (`node/.agent/context.md` or `challenge/.agent/context.md`) when working in that area

## For humans

These files are human-readable too. Edit them to change how agents behave in your project.
