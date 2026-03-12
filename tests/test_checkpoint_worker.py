"""Tests for checkpoint worker, emission checkpoint building, and report endpoints."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from crunch_node.crunch_config import (
    FRAC_64_MULTIPLIER,
    default_build_emission,
    pct_to_frac64,
)
from crunch_node.entities.prediction import (
    CheckpointRecord,
    CheckpointStatus,
    SnapshotRecord,
)
from crunch_node.workers.checkpoint_worker import CheckpointService

now = datetime.now(UTC)


# ── In-memory repos ──


class MemSnapshotRepository:
    def __init__(self, snapshots: list[SnapshotRecord] | None = None):
        self.snapshots = list(snapshots or [])

    def save(self, record: SnapshotRecord) -> None:
        self.snapshots.append(record)

    def find(self, *, model_id=None, since=None, until=None, limit=None):
        results = list(self.snapshots)
        if model_id:
            results = [s for s in results if s.model_id == model_id]
        if since:
            results = [s for s in results if s.period_end >= since]
        if until:
            results = [s for s in results if s.period_start <= until]
        return results


class MemCheckpointRepository:
    def __init__(self, checkpoints: list[CheckpointRecord] | None = None):
        self.checkpoints = list(checkpoints or [])

    def save(self, record: CheckpointRecord) -> None:
        existing = next((c for c in self.checkpoints if c.id == record.id), None)
        if existing:
            idx = self.checkpoints.index(existing)
            self.checkpoints[idx] = record
        else:
            self.checkpoints.append(record)

    def find(self, *, status=None, limit=None):
        results = list(self.checkpoints)
        if status:
            results = [c for c in results if c.status == status]
        results.sort(key=lambda c: c.created_at, reverse=True)
        if limit:
            results = results[:limit]
        return results

    def get_latest(self):
        if not self.checkpoints:
            return None
        return sorted(self.checkpoints, key=lambda c: c.created_at, reverse=True)[0]


class MemModelRepository:
    def __init__(self):
        self.models = {}

    def fetch_all(self):
        return dict(self.models)


# ── Snapshot helpers ──


def _make_snapshot(model_id: str, value: float, count: int = 10) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"SNAP_{model_id}_{now.strftime('%H%M%S')}",
        model_id=model_id,
        period_start=now - timedelta(minutes=5),
        period_end=now,
        prediction_count=count,
        result_summary={"value": value},
        created_at=now,
    )


# ── Frac64 conversion ──


class TestFrac64Conversion(unittest.TestCase):
    def test_100_pct_equals_multiplier(self):
        self.assertEqual(pct_to_frac64(100.0), FRAC_64_MULTIPLIER)

    def test_0_pct_equals_zero(self):
        self.assertEqual(pct_to_frac64(0.0), 0)

    def test_35_pct(self):
        self.assertEqual(pct_to_frac64(35.0), 350_000_000)

    def test_10_pct(self):
        self.assertEqual(pct_to_frac64(10.0), 100_000_000)


# ── Emission checkpoint building ──


class TestBuildEmission(unittest.TestCase):
    def test_single_model_gets_100pct(self):
        entries = [{"model_id": "m1", "rank": 1}]
        emission = default_build_emission(entries, crunch_pubkey="crunch123")

        self.assertEqual(emission["crunch"], "crunch123")
        self.assertEqual(len(emission["cruncher_rewards"]), 1)
        self.assertEqual(
            emission["cruncher_rewards"][0]["reward_pct"], FRAC_64_MULTIPLIER
        )

    def test_two_models_sum_to_100pct(self):
        entries = [{"model_id": "m1", "rank": 1}, {"model_id": "m2", "rank": 2}]
        emission = default_build_emission(entries, crunch_pubkey="crunch123")

        total = sum(r["reward_pct"] for r in emission["cruncher_rewards"])
        self.assertEqual(total, FRAC_64_MULTIPLIER)

    def test_ten_models_tier_distribution(self):
        entries = [{"model_id": f"m{i}", "rank": i} for i in range(1, 11)]
        emission = default_build_emission(entries, crunch_pubkey="crunch123")

        total = sum(r["reward_pct"] for r in emission["cruncher_rewards"])
        self.assertEqual(total, FRAC_64_MULTIPLIER)
        self.assertEqual(len(emission["cruncher_rewards"]), 10)

    def test_fifteen_models_with_no_remainder(self):
        """10 tier slots = 100%. Models 11-15 get 0% (no unclaimed remainder)."""
        entries = [{"model_id": f"m{i}", "rank": i} for i in range(1, 16)]
        emission = default_build_emission(entries, crunch_pubkey="crunch123")

        total = sum(r["reward_pct"] for r in emission["cruncher_rewards"])
        self.assertEqual(total, FRAC_64_MULTIPLIER)

        # Models ranked 11-15 get 0 (tiers fill exactly 100%)
        for reward in emission["cruncher_rewards"][10:]:
            self.assertEqual(reward["reward_pct"], 0)

    def test_three_models_remainder_redistributed(self):
        """With 3 models, tiers give 35+10+10=55%. Remainder 45% split equally."""
        entries = [{"model_id": f"m{i}", "rank": i} for i in range(1, 4)]
        emission = default_build_emission(entries, crunch_pubkey="crunch123")

        total = sum(r["reward_pct"] for r in emission["cruncher_rewards"])
        self.assertEqual(total, FRAC_64_MULTIPLIER)

        # All 3 models should have > 0
        for reward in emission["cruncher_rewards"]:
            self.assertGreater(reward["reward_pct"], 0)

    def test_compute_and_data_providers(self):
        entries = [{"model_id": "m1", "rank": 1}]
        emission = default_build_emission(
            entries,
            crunch_pubkey="crunch123",
            compute_provider="compute_wallet",
            data_provider="data_wallet",
        )
        self.assertEqual(len(emission["compute_provider_rewards"]), 1)
        self.assertEqual(
            emission["compute_provider_rewards"][0]["provider"], "compute_wallet"
        )
        self.assertEqual(
            emission["compute_provider_rewards"][0]["reward_pct"], FRAC_64_MULTIPLIER
        )

        self.assertEqual(len(emission["data_provider_rewards"]), 1)
        self.assertEqual(
            emission["data_provider_rewards"][0]["provider"], "data_wallet"
        )

    def test_no_providers_when_not_set(self):
        entries = [{"model_id": "m1", "rank": 1}]
        emission = default_build_emission(entries, crunch_pubkey="crunch123")
        self.assertEqual(len(emission["compute_provider_rewards"]), 0)
        self.assertEqual(len(emission["data_provider_rewards"]), 0)


# ── Checkpoint creation ──


class TestCheckpointService(unittest.TestCase):
    def test_creates_checkpoint_with_emission(self):
        snapshots = [
            _make_snapshot("m1", 0.8, count=100),
            _make_snapshot("m2", 0.6, count=50),
        ]
        snap_repo = MemSnapshotRepository(snapshots)
        ckpt_repo = MemCheckpointRepository()
        model_repo = MemModelRepository()

        service = CheckpointService(
            snapshot_repository=snap_repo,
            checkpoint_repository=ckpt_repo,
            model_repository=model_repo,
            build_emission=default_build_emission,
            crunch_pubkey="crunch_abc",
        )
        checkpoint = service.create_checkpoint()

        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.status, CheckpointStatus.PENDING)

        # entries contains one EmissionCheckpoint
        self.assertEqual(len(checkpoint.entries), 1)
        emission = checkpoint.entries[0]
        self.assertEqual(emission["crunch"], "crunch_abc")
        self.assertEqual(len(emission["cruncher_rewards"]), 2)

        # Rewards sum to FRAC_64_MULTIPLIER
        total = sum(r["reward_pct"] for r in emission["cruncher_rewards"])
        self.assertEqual(total, FRAC_64_MULTIPLIER)

        # Ranking details in meta
        self.assertIn("ranking", checkpoint.meta)
        self.assertEqual(checkpoint.meta["ranking"][0]["rank"], 1)

    def test_skips_when_no_snapshots(self):
        snap_repo = MemSnapshotRepository()
        ckpt_repo = MemCheckpointRepository()
        model_repo = MemModelRepository()

        service = CheckpointService(
            snapshot_repository=snap_repo,
            checkpoint_repository=ckpt_repo,
            model_repository=model_repo,
            build_emission=default_build_emission,
        )
        checkpoint = service.create_checkpoint()

        self.assertIsNone(checkpoint)
        self.assertEqual(len(ckpt_repo.checkpoints), 0)

    def test_period_starts_from_last_checkpoint(self):
        last = CheckpointRecord(
            id="CKP_old",
            period_start=now - timedelta(days=14),
            period_end=now - timedelta(days=7),
            status=CheckpointStatus.PAID,
        )
        snap_repo = MemSnapshotRepository([_make_snapshot("m1", 0.9)])
        ckpt_repo = MemCheckpointRepository([last])
        model_repo = MemModelRepository()

        service = CheckpointService(
            snapshot_repository=snap_repo,
            checkpoint_repository=ckpt_repo,
            model_repository=model_repo,
            build_emission=default_build_emission,
        )
        checkpoint = service.create_checkpoint()

        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.period_start, last.period_end)


# ── Report endpoint tests ──


class TestSnapshotEndpoints(unittest.TestCase):
    def test_get_snapshots(self):
        from crunch_node.workers.report_worker import get_snapshots

        snapshots = [_make_snapshot("m1", 0.8), _make_snapshot("m2", 0.6)]
        repo = MemSnapshotRepository(snapshots)
        result = get_snapshots(repo)
        self.assertEqual(len(result), 2)
        self.assertIn("result_summary", result[0])


class TestCheckpointEndpoints(unittest.TestCase):
    def _make_checkpoint(self, status=CheckpointStatus.PENDING) -> CheckpointRecord:
        return CheckpointRecord(
            id="CKP_001",
            period_start=now - timedelta(days=7),
            period_end=now,
            status=status,
            entries=[
                {
                    "crunch": "crunch_abc",
                    "cruncher_rewards": [
                        {"cruncher_index": 0, "reward_pct": FRAC_64_MULTIPLIER}
                    ],
                    "compute_provider_rewards": [],
                    "data_provider_rewards": [],
                }
            ],
            created_at=now,
        )

    def test_get_checkpoints(self):
        from crunch_node.workers.report_worker import get_checkpoints

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_checkpoints(repo)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], CheckpointStatus.PENDING)

    def test_get_latest_checkpoint(self):
        from crunch_node.workers.report_worker import get_latest_checkpoint

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_latest_checkpoint(repo)
        self.assertEqual(result["id"], "CKP_001")

    def test_get_checkpoint_payload(self):
        from crunch_node.workers.report_worker import get_checkpoint_payload

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_checkpoint_payload("CKP_001", repo)
        self.assertIn("entries", result)
        self.assertEqual(result["entries"][0]["crunch"], "crunch_abc")

    def test_confirm_checkpoint_sets_submitted(self):
        from crunch_node.workers.report_worker import confirm_checkpoint

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = confirm_checkpoint("CKP_001", {"tx_hash": "0xabc"}, repo)
        self.assertEqual(result["status"], CheckpointStatus.SUBMITTED)
        self.assertEqual(result["tx_hash"], "0xabc")

    def test_confirm_rejects_non_pending(self):
        from fastapi import HTTPException

        from crunch_node.workers.report_worker import confirm_checkpoint

        repo = MemCheckpointRepository(
            [self._make_checkpoint(status=CheckpointStatus.SUBMITTED)]
        )
        with self.assertRaises(HTTPException):
            confirm_checkpoint("CKP_001", {"tx_hash": "0xabc"}, repo)

    def test_status_transition_submitted_to_claimable(self):
        from crunch_node.workers.report_worker import update_checkpoint_status

        repo = MemCheckpointRepository(
            [self._make_checkpoint(status=CheckpointStatus.SUBMITTED)]
        )
        result = update_checkpoint_status("CKP_001", {"status": "CLAIMABLE"}, repo)
        self.assertEqual(result["status"], CheckpointStatus.CLAIMABLE)

    def test_invalid_status_transition_rejected(self):
        from fastapi import HTTPException

        from crunch_node.workers.report_worker import update_checkpoint_status

        repo = MemCheckpointRepository(
            [self._make_checkpoint(status=CheckpointStatus.PENDING)]
        )
        with self.assertRaises(HTTPException):
            update_checkpoint_status("CKP_001", {"status": "PAID"}, repo)


class TestEmissionEndpoints(unittest.TestCase):
    def _make_checkpoint(self) -> CheckpointRecord:
        return CheckpointRecord(
            id="CKP_001",
            period_start=now - timedelta(days=7),
            period_end=now,
            status=CheckpointStatus.PENDING,
            entries=[
                {
                    "crunch": "crunch_abc",
                    "cruncher_rewards": [
                        {"cruncher_index": 0, "reward_pct": 600_000_000},
                        {"cruncher_index": 1, "reward_pct": 400_000_000},
                    ],
                    "compute_provider_rewards": [
                        {
                            "provider": "compute_wallet",
                            "reward_pct": FRAC_64_MULTIPLIER,
                        },
                    ],
                    "data_provider_rewards": [],
                }
            ],
            meta={
                "ranking": [
                    {"model_id": "m1", "rank": 1},
                    {"model_id": "m2", "rank": 2},
                ]
            },
            created_at=now,
        )

    def test_get_emission_returns_protocol_format(self):
        from crunch_node.workers.report_worker import get_checkpoint_emission

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_checkpoint_emission("CKP_001", repo)

        self.assertEqual(result["crunch"], "crunch_abc")
        self.assertEqual(len(result["cruncher_rewards"]), 2)
        total = sum(r["reward_pct"] for r in result["cruncher_rewards"])
        self.assertEqual(total, FRAC_64_MULTIPLIER)

    def test_get_emission_cli_format(self):
        from crunch_node.workers.report_worker import (
            get_checkpoint_emission_cli_format,
        )

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_checkpoint_emission_cli_format("CKP_001", repo)

        self.assertEqual(result["crunch"], "crunch_abc")
        # crunchEmission keyed by model_id with percentages
        self.assertAlmostEqual(result["crunchEmission"]["m1"], 60.0, places=3)
        self.assertAlmostEqual(result["crunchEmission"]["m2"], 40.0, places=3)
        # compute provider
        self.assertAlmostEqual(
            result["computeProvider"]["compute_wallet"], 100.0, places=3
        )
        # no data provider
        self.assertEqual(len(result["dataProvider"]), 0)

    def test_get_latest_emission(self):
        from crunch_node.workers.report_worker import get_latest_emission

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_latest_emission(repo)

        self.assertEqual(result["checkpoint_id"], "CKP_001")
        self.assertIn("emission", result)
        self.assertEqual(result["emission"]["crunch"], "crunch_abc")


# ── Prizes endpoint tests ──


class TestPrizesEndpoints(unittest.TestCase):
    def _make_checkpoint(self) -> CheckpointRecord:
        return CheckpointRecord(
            id="CKP_001",
            period_start=now - timedelta(days=7),
            period_end=now,
            status=CheckpointStatus.PENDING,
            entries=[
                {
                    "crunch": "crunch_abc",
                    "cruncher_rewards": [
                        {"cruncher_index": 0, "reward_pct": 600_000_000},
                        {"cruncher_index": 1, "reward_pct": 400_000_000},
                    ],
                    "compute_provider_rewards": [],
                    "data_provider_rewards": [],
                }
            ],
            meta={
                "ranking": [
                    {"model_id": "m1", "rank": 1},
                    {"model_id": "m2", "rank": 2},
                ]
            },
            created_at=now,
        )

    def test_get_checkpoint_prizes_format(self):
        from crunch_node.workers.report_worker import get_checkpoint_prizes

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_checkpoint_prizes("CKP_001", repo, total_prize=1_000_000)

        self.assertEqual(len(result), 2)
        # First model gets 60%
        self.assertEqual(result[0]["model"], "m1")
        self.assertEqual(result[0]["prize"], 600_000)
        self.assertEqual(result[0]["prizeId"], "CKP_001-m1")
        self.assertIn("timestamp", result[0])
        # Second model gets 40%
        self.assertEqual(result[1]["model"], "m2")
        self.assertEqual(result[1]["prize"], 400_000)

    def test_get_checkpoint_prizes_zero_total(self):
        from crunch_node.workers.report_worker import get_checkpoint_prizes

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_checkpoint_prizes("CKP_001", repo, total_prize=0)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["prize"], 0)
        self.assertEqual(result[1]["prize"], 0)

    def test_get_checkpoint_prizes_not_found(self):
        from fastapi import HTTPException

        from crunch_node.workers.report_worker import get_checkpoint_prizes

        repo = MemCheckpointRepository()
        with self.assertRaises(HTTPException):
            get_checkpoint_prizes("CKP_NONEXISTENT", repo, total_prize=1000)

    def test_get_checkpoint_prizes_sums_to_total(self):
        from crunch_node.workers.report_worker import get_checkpoint_prizes

        repo = MemCheckpointRepository([self._make_checkpoint()])
        total = 999_999
        result = get_checkpoint_prizes("CKP_001", repo, total_prize=total)

        actual_sum = sum(p["prize"] for p in result)
        # May differ by ±1 due to rounding, but should be close
        self.assertAlmostEqual(actual_sum, total, delta=len(result))

    def test_get_latest_checkpoint_prizes(self):
        from crunch_node.workers.report_worker import get_latest_checkpoint_prizes

        repo = MemCheckpointRepository([self._make_checkpoint()])
        result = get_latest_checkpoint_prizes(repo, total_prize=1_000_000)

        self.assertEqual(result["checkpoint_id"], "CKP_001")
        self.assertEqual(result["total_prize"], 1_000_000)
        self.assertEqual(len(result["prizes"]), 2)
        self.assertEqual(result["prizes"][0]["model"], "m1")
        self.assertEqual(result["prizes"][0]["prize"], 600_000)

    def test_get_latest_checkpoint_prizes_not_found(self):
        from fastapi import HTTPException

        from crunch_node.workers.report_worker import get_latest_checkpoint_prizes

        repo = MemCheckpointRepository()
        with self.assertRaises(HTTPException):
            get_latest_checkpoint_prizes(repo, total_prize=1000)


if __name__ == "__main__":
    unittest.main()
