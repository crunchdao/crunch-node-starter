---
name: deploy
description: "Deploy a crunch coordinator node to any environment. Use when setting up, redeploying, or troubleshooting any server deployment."
---

## Pre-deploy Checklist

Before deploying to ANY environment, verify ALL of the following are configured. Missing any causes silent failures that are hard to diagnose.

### Environment Files

The Makefile passes `--env-file .local.env` to docker-compose. **Always deploy via `make deploy`** — raw `docker compose up` skips `.local.env` and causes silent misconfiguration (init-db falls back to wrong base defaults).

| File | Loaded by | Purpose |
|---|---|---|
| `node/.local.env` | Makefile `--env-file` | **All config**: feed, API keys, scoring, timing |
| `node/.env` | Docker Compose auto-loads | Optional overrides (not required if .local.env has everything) |

### Required Variables in `.local.env`

```env
# Competition identity
CRUNCH_ID=<competition-name>
MODEL_BASE_CLASSNAME=<package>.tracker.TrackerBase

# Feed
FEED_SOURCE=<source>
FEED_SUBJECTS=<subjects>
FEED_KIND=<kind>
FEED_GRANULARITY=<granularity>

# External model orchestrator (if not using local)
MODEL_RUNNER_NODE_HOST=<orchestrator-hostname>
MODEL_RUNNER_NODE_PORT=9091
```

### What breaks when each is missing

| Variable | Failure mode |
|---|---|
| `MODEL_BASE_CLASSNAME` | **Models fail with `BAD_IMPLEMENTATION`**. The default (`cruncher.ModelBaseClass`) only works for the scaffold. Every project must set this to `<package>.tracker.TrackerBase` where `<package>` is the challenge package name. |
| `FEED_SOURCE` | init-db seeds wrong prediction config from base defaults. Predict-worker uses scaffold default (binance candles) instead of the project's feed. |
| `MODEL_RUNNER_NODE_HOST` | Predict-worker tries to connect to the internal `model-orchestrator` Docker container. If using an external orchestrator, this must point to its hostname. |
| `CRUNCH_ID` | Defaults to `starter-challenge`. Must match the competition ID on the orchestrator. |

### mTLS Certificates (for secure model connections)

When the orchestrator uses `is-secure: true`, the predict-worker needs mTLS certs:

1. Set `SECURE_CERT_DIR=/certs` in the predict-worker environment
2. Mount the cert directory: `/path/to/certs:/certs:ro`
3. Required files: `ca.crt`, `tls.crt`, `tls.key`
4. The cert's public key hash must be registered on-chain for the coordinator wallet

**After registering a new cert hash, model containers must be restarted** — the hash is passed as an env var at ECS task start time, not read dynamically.

Check cert hash:
```bash
openssl x509 -in tls.crt -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum
```

## Compose Override for External Orchestrator

When using an external model orchestrator (AWS ECS), the local model-orchestrator service must be stubbed out and the predict-worker needs cert mounts:

```yaml
services:
  model-orchestrator:
    image: busybox:latest
    entrypoint: ["true"]
    command: []
    restart: "no"
    volumes: []
    networks: [backend]

  predict-worker:
    environment:
      SECURE_CERT_DIR: /certs
    volumes:
      - /path/to/certs:/certs:ro
```

The Makefile auto-detects override files via `docker-compose.override.yml`. Symlink the per-environment override:
```bash
cd node
ln -sf <path-to-override> docker-compose.override.yml
```

## Directory Layout

```
/home/ubuntu/
├── app/<project>/         # Git clone
│   ├── node/              # docker-compose.yml, Makefile, .local.env
│   ├── challenge/         # Challenge package
│   └── webapp/            # Report UI (clone of coordinator-webapp)
└── certs/                 # mTLS certs (ca.crt, tls.crt, tls.key)
```

## Deploy Steps

**IMPORTANT: Always use `make deploy`. Never use raw `docker compose up`.** The Makefile passes `--env-file .local.env` which is required for correct configuration. Raw docker compose skips `.local.env`, causing init-db to fall back to wrong defaults.

### Fresh deploy

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker ubuntu

# 2. Clone project and webapp
mkdir -p /home/ubuntu/app && cd /home/ubuntu/app
git clone git@github.com:<org>/<repo>.git
cd <repo>
git clone git@github.com:crunchdao/coordinator-webapp.git webapp

# 3. Copy mTLS certs to /home/ubuntu/certs/

# 4. Create node/.local.env — use the checklist above, set EVERY variable

# 5. Symlink the compose override
cd node
ln -sf <path-to-override> docker-compose.override.yml

# 6. Deploy
make deploy
```

### Updating code

```bash
cd /home/ubuntu/app/<project>
git pull
cd node
make deploy
```

### Restarting services

```bash
cd node
make down
make deploy
```

### Reset database

```bash
cd node
make reset-db
make deploy
```

## Common Issues

### Model fails with BAD_IMPLEMENTATION
`MODEL_BASE_CLASSNAME` is wrong. Must be the fully qualified class path: `<package>.tracker.TrackerBase`. Check what the challenge package exports.

### Predict-worker shows connection timeout to models
- ECS tasks need `assign-public-ip: true` if the coordinator is outside the VPC
- Security group must allow inbound TCP 50051 from the coordinator server IP

### Predict-worker shows cert errors
- `SECURE_CERT_DIR` not set or certs not mounted
- Cert hash not registered on-chain — check with the certificates API
- Models need restart after cert hash registration

### Score worker says "No predictions scored this cycle"
- No models connected (check predict-worker logs)
- Predictions haven't reached their `resolvable_at` time
- Ground truth resolver can't determine outcomes (check resolver logs)

### init-db fails with lock timeout
Score-worker holds a DB lock. Stop it first, then redeploy.

### webapp build fails
The `webapp/` directory is missing. Clone `coordinator-webapp` as a sibling.

## ECS Model Requirements

For models running on AWS ECS via an external orchestrator:
- `assign-public-ip: true` in orchestrator crunch config
- Security group allows TCP 50051 inbound from coordinator IP
- `is-secure: true` requires mTLS certs registered on-chain
- Coordinator cert hash must match primary or secondary on-chain hash
