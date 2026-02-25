from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

SERVICES = (
    "feed-data-worker",
    "predict-worker",
    "score-worker",
    "checkpoint-worker",
    "report-worker",
    "model-orchestrator",
    "postgres",
)


def _read_logs(tail: int) -> str:
    cmd = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "--env-file",
        ".local.env",
        "logs",
        "--tail",
        str(tail),
        *SERVICES,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (result.stdout or "") + "\n" + (result.stderr or "")


def _parse_line(line: str) -> dict[str, str]:
    if "|" in line:
        left, right = line.split("|", 1)
        return {"service": left.strip(), "message": right.strip()}
    return {"service": "unknown", "message": line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture runtime service logs to JSONL"
    )
    parser.add_argument("--output", default="runtime-services.jsonl")
    parser.add_argument("--tail", type=int, default=2000)
    args = parser.parse_args()

    raw = _read_logs(tail=args.tail)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for row in raw.splitlines():
        row = row.strip()
        if not row:
            continue
        parsed = _parse_line(row)
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": parsed["service"],
            "message": parsed["message"],
        }
        lines.append(json.dumps(payload, separators=(",", ":")))

    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"captured {len(lines)} log lines -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
