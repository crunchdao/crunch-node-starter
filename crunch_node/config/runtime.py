from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeSettings:
    checkpoint_interval_seconds: int
    score_interval_seconds: int
    model_runner_node_host: str
    model_runner_node_port: int
    model_runner_timeout_seconds: float
    crunch_id: str
    crunch_pubkey: str
    network: str
    base_classname: str
    feed_provider: str
    feed_record_ttl_days: int
    gateway_cert_dir: str | None
    secure_cert_dir: str | None

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        checkpoint = int(os.getenv("CHECKPOINT_INTERVAL_SECONDS", "900"))
        return cls(
            checkpoint_interval_seconds=checkpoint,
            score_interval_seconds=int(
                os.getenv("SCORE_INTERVAL_SECONDS", str(min(60, checkpoint)))
            ),
            model_runner_node_host=os.getenv(
                "MODEL_RUNNER_NODE_HOST", "model-orchestrator"
            ),
            model_runner_node_port=int(os.getenv("MODEL_RUNNER_NODE_PORT", "9091")),
            model_runner_timeout_seconds=float(
                os.getenv("MODEL_RUNNER_TIMEOUT_SECONDS", "60")
            ),
            crunch_id=os.getenv("CRUNCH_ID", "starter-challenge"),
            crunch_pubkey=os.getenv("CRUNCH_PUBKEY", ""),
            network=os.getenv("NETWORK", "devnet"),
            base_classname=os.getenv("MODEL_BASE_CLASSNAME", "cruncher.ModelBaseClass"),
            feed_provider=os.getenv("FEED_PROVIDER", "binance"),
            feed_record_ttl_days=int(os.getenv("FEED_RECORD_TTL_DAYS", "90")),
            gateway_cert_dir=os.getenv("GATEWAY_CERT_DIR"),
            secure_cert_dir=os.getenv("SECURE_CERT_DIR"),
        )
