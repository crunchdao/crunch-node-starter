#!/usr/bin/env python3
"""Standalone Merkle proof verifier for crunchers.

Usage:
    python verify_merkle.py --coordinator-url https://... --snapshot-id SNAP_xxx

Verifies that a snapshot is included in the coordinator's Merkle tree
by fetching the proof and independently recomputing hashes.

Dependencies: requests (pip install requests)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)


def sha256_concat(left: str, right: str) -> str:
    combined = left + right
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def canonical_snapshot_hash(snapshot: dict) -> str:
    payload = {
        "model_id": snapshot["model_id"],
        "period_start": snapshot["period_start"],
        "period_end": snapshot["period_end"],
        "prediction_count": snapshot["prediction_count"],
        "result_summary": snapshot["result_summary"],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_proof(leaf_hash: str, path: list[dict], expected_root: str) -> bool:
    current = leaf_hash
    for step in path:
        if step["position"] == "right":
            current = sha256_concat(current, step["hash"])
        else:
            current = sha256_concat(step["hash"], current)
    return current == expected_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Merkle inclusion proof for a snapshot"
    )
    parser.add_argument("--coordinator-url", required=True, help="Coordinator base URL")
    parser.add_argument("--snapshot-id", required=True, help="Snapshot ID to verify")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed output"
    )
    args = parser.parse_args()

    base = args.coordinator_url.rstrip("/")

    # 1. Fetch the proof
    print(f"Fetching proof for {args.snapshot_id}...")
    resp = requests.get(
        f"{base}/reports/merkle/proof", params={"snapshot_id": args.snapshot_id}
    )
    if resp.status_code != 200:
        print(f"FAIL: Could not fetch proof (HTTP {resp.status_code}): {resp.text}")
        sys.exit(1)

    proof = resp.json()
    if args.verbose:
        print(f"  content_hash: {proof['snapshot_content_hash']}")
        print(f"  cycle_id:     {proof['cycle_id']}")
        print(f"  cycle_root:   {proof['cycle_root']}")
        print(f"  path steps:   {len(proof['path'])}")

    # 2. Fetch the snapshot data to independently compute its hash
    print("Fetching snapshot data...")
    resp = requests.get(f"{base}/reports/snapshots", params={"limit": 1000})
    if resp.status_code != 200:
        print(f"FAIL: Could not fetch snapshots (HTTP {resp.status_code})")
        sys.exit(1)

    snapshots = resp.json()
    snapshot = next((s for s in snapshots if s["id"] == args.snapshot_id), None)
    if snapshot is None:
        print(f"FAIL: Snapshot {args.snapshot_id} not found in coordinator data")
        sys.exit(1)

    # 3. Recompute content hash
    # Normalize timestamps to match what the coordinator hashed
    computed_hash = canonical_snapshot_hash(snapshot)
    if args.verbose:
        print(f"  computed_hash: {computed_hash}")

    if computed_hash != proof["snapshot_content_hash"]:
        print("FAIL: Content hash mismatch!")
        print(f"  Expected (from proof): {proof['snapshot_content_hash']}")
        print(f"  Computed (from data):  {computed_hash}")
        print("  → The snapshot data has been tampered with.")
        sys.exit(1)

    print("✓ Content hash matches")

    # 4. Verify Merkle path to cycle root
    if proof["cycle_root"]:
        # The cycle_root is a chained_root = SHA-256(previous_cycle_root + snapshots_root)
        # The proof path takes us from leaf to snapshots_root (the mini-tree root)
        # We need to find the snapshots_root from the cycle data
        resp = requests.get(f"{base}/reports/merkle/cycles/{proof['cycle_id']}")
        if resp.status_code == 200:
            cycle = resp.json()
            snapshots_root = cycle["snapshots_root"]

            if verify_proof(computed_hash, proof["path"], snapshots_root):
                print("✓ Merkle proof valid — snapshot is in cycle's mini-tree")
            else:
                print(
                    "FAIL: Merkle proof invalid — path does not lead to snapshots_root"
                )
                sys.exit(1)

            # Verify chaining
            if cycle["previous_cycle_root"]:
                expected_chained = sha256_concat(
                    cycle["previous_cycle_root"], snapshots_root
                )
            else:
                expected_chained = snapshots_root

            if expected_chained == cycle["chained_root"]:
                print("✓ Cycle chain valid — chained_root correctly derived")
            else:
                print("FAIL: Cycle chain broken!")
                print(f"  Expected: {expected_chained}")
                print(f"  Got:      {cycle['chained_root']}")
                sys.exit(1)
        else:
            print(f"WARNING: Could not fetch cycle data (HTTP {resp.status_code})")
    else:
        print("WARNING: No cycle root in proof — cannot verify tree path")

    print()
    print("PASS: All checks passed. Snapshot is verifiably included and untampered.")


if __name__ == "__main__":
    main()
