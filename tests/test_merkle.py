"""Tests for Merkle tree tamper evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from crunch_node.db.tables.merkle import MerkleCycleRow, MerkleNodeRow
from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.merkle.hasher import canonical_snapshot_hash, sha256_concat
from crunch_node.merkle.service import MerkleService
from crunch_node.merkle.tree import (
    MerkleNode,
    build_merkle_tree,
    generate_proof,
    get_root,
    verify_proof,
)

# ── Hasher tests ──


class TestCanonicalSnapshotHash:
    def test_deterministic(self):
        """Same inputs always produce the same hash."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        h1 = canonical_snapshot_hash("model_a", now, now, 10, {"mae": 0.5})
        h2 = canonical_snapshot_hash("model_a", now, now, 10, {"mae": 0.5})
        assert h1 == h2

    def test_different_model_id(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        h1 = canonical_snapshot_hash("model_a", now, now, 10, {"mae": 0.5})
        h2 = canonical_snapshot_hash("model_b", now, now, 10, {"mae": 0.5})
        assert h1 != h2

    def test_different_prediction_count(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        h1 = canonical_snapshot_hash("model_a", now, now, 10, {"mae": 0.5})
        h2 = canonical_snapshot_hash("model_a", now, now, 11, {"mae": 0.5})
        assert h1 != h2

    def test_different_result_summary(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        h1 = canonical_snapshot_hash("model_a", now, now, 10, {"mae": 0.5})
        h2 = canonical_snapshot_hash("model_a", now, now, 10, {"mae": 0.6})
        assert h1 != h2

    def test_result_summary_key_order_irrelevant(self):
        """JSON sort_keys ensures order doesn't matter."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        h1 = canonical_snapshot_hash("m", now, now, 1, {"a": 1, "b": 2})
        h2 = canonical_snapshot_hash("m", now, now, 1, {"b": 2, "a": 1})
        assert h1 == h2

    def test_returns_hex_string(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        h = canonical_snapshot_hash("m", now, now, 1, {})
        assert len(h) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in h)


class TestSha256Concat:
    def test_deterministic(self):
        h1 = sha256_concat("abc", "def")
        h2 = sha256_concat("abc", "def")
        assert h1 == h2

    def test_order_matters(self):
        h1 = sha256_concat("abc", "def")
        h2 = sha256_concat("def", "abc")
        assert h1 != h2


# ── Tree tests ──


class TestBuildMerkleTree:
    def test_empty(self):
        assert build_merkle_tree([]) == []

    def test_single_leaf(self):
        leaf = MerkleNode(hash="aaa", level=0, position=0)
        nodes = build_merkle_tree([leaf])
        assert len(nodes) == 1
        assert get_root(nodes).hash == "aaa"

    def test_two_leaves(self):
        leaves = [
            MerkleNode(hash="aaa", level=0, position=0),
            MerkleNode(hash="bbb", level=0, position=1),
        ]
        nodes = build_merkle_tree(leaves)
        root = get_root(nodes)
        assert root is not None
        assert root.level == 1
        assert root.hash == sha256_concat("aaa", "bbb")

    def test_three_leaves_padding(self):
        """Odd number of leaves: last is duplicated."""
        leaves = [
            MerkleNode(hash="a", level=0, position=0),
            MerkleNode(hash="b", level=0, position=1),
            MerkleNode(hash="c", level=0, position=2),
        ]
        nodes = build_merkle_tree(leaves)
        root = get_root(nodes)
        assert root is not None
        # Level 1: hash(a,b), hash(c,c)
        # Level 2: hash(hash(a,b), hash(c,c))
        ab = sha256_concat("a", "b")
        cc = sha256_concat("c", "c")
        expected_root = sha256_concat(ab, cc)
        assert root.hash == expected_root

    def test_four_leaves(self):
        leaves = [MerkleNode(hash=f"leaf{i}", level=0, position=i) for i in range(4)]
        nodes = build_merkle_tree(leaves)
        root = get_root(nodes)
        l01 = sha256_concat("leaf0", "leaf1")
        l23 = sha256_concat("leaf2", "leaf3")
        assert root.hash == sha256_concat(l01, l23)

    def test_proof_and_verify(self):
        """End-to-end: build tree, generate proof, verify it."""
        leaves = [MerkleNode(hash=f"h{i}", level=0, position=i) for i in range(5)]
        nodes = build_merkle_tree(leaves)
        root = get_root(nodes)

        for leaf in leaves:
            proof = generate_proof(nodes, leaf.hash)
            assert verify_proof(leaf.hash, proof, root.hash), (
                f"Proof failed for {leaf.hash}"
            )

    def test_invalid_proof_fails(self):
        leaves = [
            MerkleNode(hash="x", level=0, position=0),
            MerkleNode(hash="y", level=0, position=1),
        ]
        nodes = build_merkle_tree(leaves)
        root = get_root(nodes)
        proof = generate_proof(nodes, "x")
        assert not verify_proof("x", proof, "wrong_root")

    def test_tampered_leaf_fails(self):
        leaves = [
            MerkleNode(hash="x", level=0, position=0),
            MerkleNode(hash="y", level=0, position=1),
        ]
        nodes = build_merkle_tree(leaves)
        root = get_root(nodes)
        proof = generate_proof(nodes, "x")
        # Try to verify with a tampered leaf hash
        assert not verify_proof("tampered", proof, root.hash)


# ── In-memory repository stubs for MerkleService tests ──


class InMemoryMerkleCycleRepository:
    def __init__(self):
        self._cycles: dict[str, MerkleCycleRow] = {}

    def save(self, cycle: MerkleCycleRow) -> None:
        self._cycles[cycle.id] = cycle

    def get(self, cycle_id: str) -> MerkleCycleRow | None:
        return self._cycles.get(cycle_id)

    def get_latest(self) -> MerkleCycleRow | None:
        if not self._cycles:
            return None
        return max(self._cycles.values(), key=lambda c: c.created_at)

    def find(self, *, since=None, until=None, limit=None) -> list[MerkleCycleRow]:
        cycles = list(self._cycles.values())
        if since:
            cycles = [c for c in cycles if c.created_at >= since]
        if until:
            cycles = [c for c in cycles if c.created_at <= until]
        cycles.sort(key=lambda c: c.created_at)
        if limit:
            cycles = cycles[:limit]
        return cycles


class InMemoryMerkleNodeRepository:
    def __init__(self):
        self._nodes: dict[str, MerkleNodeRow] = {}

    def save(self, node: MerkleNodeRow) -> None:
        self._nodes[node.id] = node

    def find_by_cycle_id(self, cycle_id: str) -> list[MerkleNodeRow]:
        return sorted(
            [n for n in self._nodes.values() if n.cycle_id == cycle_id],
            key=lambda n: (n.level, n.position),
        )

    def find_by_checkpoint_id(self, checkpoint_id: str) -> list[MerkleNodeRow]:
        return sorted(
            [n for n in self._nodes.values() if n.checkpoint_id == checkpoint_id],
            key=lambda n: (n.level, n.position),
        )

    def find_by_snapshot_id(self, snapshot_id: str) -> MerkleNodeRow | None:
        for n in self._nodes.values():
            if n.snapshot_id == snapshot_id:
                return n
        return None

    def find_by_hash_in_checkpoint(self, hash_value: str) -> list[MerkleNodeRow]:
        return [
            n
            for n in self._nodes.values()
            if n.checkpoint_id is not None and n.hash == hash_value and n.level == 0
        ]


# ── MerkleService tests ──


def _make_snapshot(model_id: str, now: datetime, count: int = 5) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"SNAP_{model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
        model_id=model_id,
        period_start=now,
        period_end=now,
        prediction_count=count,
        result_summary={"mae": 0.1 * count},
        created_at=now,
    )


class TestMerkleService:
    def _make_service(self):
        cycle_repo = InMemoryMerkleCycleRepository()
        node_repo = InMemoryMerkleNodeRepository()
        return MerkleService(cycle_repo, node_repo), cycle_repo, node_repo

    def test_commit_cycle_no_snapshots(self):
        svc, _, _ = self._make_service()
        assert svc.commit_cycle([]) is None

    def test_commit_cycle_single_snapshot(self):
        svc, cycle_repo, node_repo = self._make_service()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        snap = _make_snapshot("model_a", now)

        cycle = svc.commit_cycle([snap], now)
        assert cycle is not None
        assert cycle.snapshot_count == 1
        assert cycle.previous_cycle_id is None
        assert cycle.snapshots_root == cycle.chained_root  # first cycle, no chaining

        # Node was saved
        nodes = node_repo.find_by_cycle_id(cycle.id)
        assert len(nodes) == 1
        assert nodes[0].snapshot_id == snap.id

    def test_commit_cycle_multiple_snapshots(self):
        svc, cycle_repo, node_repo = self._make_service()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        snaps = [_make_snapshot(f"model_{i}", now, count=i + 1) for i in range(3)]

        cycle = svc.commit_cycle(snaps, now)
        assert cycle is not None
        assert cycle.snapshot_count == 3

        nodes = node_repo.find_by_cycle_id(cycle.id)
        leaves = [n for n in nodes if n.level == 0]
        assert len(leaves) == 3

    def test_cycle_chaining(self):
        svc, cycle_repo, _ = self._make_service()
        now1 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now2 = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

        snap1 = _make_snapshot("m1", now1)
        snap2 = _make_snapshot("m2", now2)

        cycle1 = svc.commit_cycle([snap1], now1)
        cycle2 = svc.commit_cycle([snap2], now2)

        assert cycle2.previous_cycle_id == cycle1.id
        assert cycle2.previous_cycle_root == cycle1.chained_root
        assert cycle2.chained_root == sha256_concat(
            cycle1.chained_root, cycle2.snapshots_root
        )

    def test_commit_checkpoint(self):
        svc, cycle_repo, node_repo = self._make_service()
        now1 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now2 = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        now3 = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)

        svc.commit_cycle([_make_snapshot("m1", now1)], now1)
        svc.commit_cycle([_make_snapshot("m2", now2)], now2)

        root = svc.commit_checkpoint(
            checkpoint_id="CKP_test",
            period_start=now1,
            period_end=now3,
            now=now3,
        )

        assert root is not None
        assert len(root) == 64  # SHA-256 hex

        # Check checkpoint nodes were saved
        cp_nodes = node_repo.find_by_checkpoint_id("CKP_test")
        assert len(cp_nodes) > 0

    def test_get_proof(self):
        svc, cycle_repo, node_repo = self._make_service()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        snaps = [_make_snapshot(f"m{i}", now) for i in range(4)]

        cycle = svc.commit_cycle(snaps, now)

        for snap in snaps:
            proof = svc.get_proof(snap.id)
            assert proof is not None
            assert proof.snapshot_id == snap.id
            assert proof.cycle_id == cycle.id

            # Verify the proof leads to snapshots_root
            current = proof.snapshot_content_hash
            for step in proof.path:
                if step.position == "right":
                    current = sha256_concat(current, step.hash)
                else:
                    current = sha256_concat(step.hash, current)
            assert current == cycle.snapshots_root

    def test_proof_not_found(self):
        svc, _, _ = self._make_service()
        assert svc.get_proof("nonexistent") is None

    def test_tamper_detection(self):
        """If snapshot content changes, the content hash won't match the Merkle leaf."""
        svc, _, node_repo = self._make_service()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        snap = _make_snapshot("m1", now)

        svc.commit_cycle([snap], now)

        # Get the committed hash
        leaf = node_repo.find_by_snapshot_id(snap.id)
        committed_hash = leaf.snapshot_content_hash

        # "Tamper" with the snapshot
        snap.result_summary["mae"] = 999.0
        tampered_hash = canonical_snapshot_hash(
            snap.model_id,
            snap.period_start,
            snap.period_end,
            snap.prediction_count,
            snap.result_summary,
        )

        # Hashes don't match → tamper detected
        assert committed_hash != tampered_hash
