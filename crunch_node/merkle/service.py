"""Merkle service: commit cycles, commit checkpoints, generate proofs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from crunch_node.db.tables.merkle import MerkleCycleRow, MerkleNodeRow
from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.merkle.hasher import canonical_snapshot_hash, sha256_concat
from crunch_node.merkle.tree import (
    MerkleNode,
    MerkleProof,
    build_merkle_tree,
    generate_proof,
    get_root,
)

logger = logging.getLogger(__name__)


class MerkleService:
    """Manages Merkle tree construction for tamper evidence.

    - commit_cycle(): called after each score cycle, builds a mini-tree
      over the cycle's snapshots and chains to the previous cycle.
    - commit_checkpoint(): called at checkpoint time, builds a tree
      over all cycle roots since the last checkpoint.
    - get_proof(): generates an inclusion proof for a specific snapshot.
    """

    def __init__(
        self,
        merkle_cycle_repository: DBMerkleCycleRepository,
        merkle_node_repository: DBMerkleNodeRepository,
    ):
        self.cycle_repo = merkle_cycle_repository
        self.node_repo = merkle_node_repository

    def commit_cycle(
        self,
        snapshots: list[SnapshotRecord],
        now: datetime | None = None,
    ) -> MerkleCycleRow | None:
        """Build a Merkle tree over this cycle's snapshots and chain to previous.

        Returns the MerkleCycleRow, or None if no snapshots.
        """
        if not snapshots:
            return None

        now = now or datetime.now(UTC)
        cycle_id = f"CYC_{now.strftime('%Y%m%d_%H%M%S_%f')}"

        # Compute content hashes and build leaves (sorted by model_id for determinism)
        sorted_snapshots = sorted(snapshots, key=lambda s: s.model_id)
        leaves: list[MerkleNode] = []
        for i, snap in enumerate(sorted_snapshots):
            content_hash = canonical_snapshot_hash(
                model_id=snap.model_id,
                period_start=snap.period_start,
                period_end=snap.period_end,
                prediction_count=snap.prediction_count,
                result_summary=snap.result_summary,
            )
            leaves.append(
                MerkleNode(
                    hash=content_hash,
                    level=0,
                    position=i,
                    snapshot_id=snap.id,
                    snapshot_content_hash=content_hash,
                )
            )

        # Build tree
        all_nodes = build_merkle_tree(leaves)
        root = get_root(all_nodes)
        snapshots_root = root.hash if root else leaves[0].hash

        # Chain to previous cycle
        previous = self.cycle_repo.get_latest()
        previous_id = previous.id if previous else None
        previous_root = previous.chained_root if previous else None

        if previous_root:
            chained_root = sha256_concat(previous_root, snapshots_root)
        else:
            chained_root = snapshots_root

        # Save cycle
        cycle = MerkleCycleRow(
            id=cycle_id,
            previous_cycle_id=previous_id,
            previous_cycle_root=previous_root,
            snapshots_root=snapshots_root,
            chained_root=chained_root,
            snapshot_count=len(sorted_snapshots),
            created_at=now,
        )
        self.cycle_repo.save(cycle)

        # Save tree nodes
        for node in all_nodes:
            node_row = MerkleNodeRow(
                id=f"MRK_{cycle_id}_{node.level}_{node.position}",
                cycle_id=cycle_id,
                checkpoint_id=None,
                level=node.level,
                position=node.position,
                hash=node.hash,
                left_child_id=(
                    f"MRK_{cycle_id}_{node.left.level}_{node.left.position}"
                    if node.left
                    else None
                ),
                right_child_id=(
                    f"MRK_{cycle_id}_{node.right.level}_{node.right.position}"
                    if node.right
                    else None
                ),
                snapshot_id=node.snapshot_id,
                snapshot_content_hash=node.snapshot_content_hash,
                created_at=now,
            )
            self.node_repo.save(node_row)

        logger.info(
            "Merkle cycle %s: %d snapshots, snapshots_root=%s, chained_root=%s",
            cycle_id,
            len(sorted_snapshots),
            snapshots_root[:16],
            chained_root[:16],
        )
        return cycle

    def commit_checkpoint(
        self,
        checkpoint_id: str,
        period_start: datetime,
        period_end: datetime,
        now: datetime | None = None,
    ) -> str | None:
        """Build a Merkle tree over cycle roots since last checkpoint.

        Returns the merkle_root hash, or None if no cycles.
        """
        now = now or datetime.now(UTC)

        cycles = self.cycle_repo.find(since=period_start, until=period_end)
        if not cycles:
            logger.info("No Merkle cycles for checkpoint %s", checkpoint_id)
            return None

        # Leaves are cycle chained_roots, ordered by creation time
        sorted_cycles = sorted(cycles, key=lambda c: c.created_at)
        leaves: list[MerkleNode] = []
        for i, cycle in enumerate(sorted_cycles):
            leaves.append(
                MerkleNode(
                    hash=cycle.chained_root,
                    level=0,
                    position=i,
                )
            )

        all_nodes = build_merkle_tree(leaves)
        root = get_root(all_nodes)
        merkle_root = root.hash if root else leaves[0].hash

        # Save checkpoint tree nodes
        for node in all_nodes:
            node_row = MerkleNodeRow(
                id=f"MRK_{checkpoint_id}_{node.level}_{node.position}",
                checkpoint_id=checkpoint_id,
                cycle_id=None,
                level=node.level,
                position=node.position,
                hash=node.hash,
                left_child_id=(
                    f"MRK_{checkpoint_id}_{node.left.level}_{node.left.position}"
                    if node.left
                    else None
                ),
                right_child_id=(
                    f"MRK_{checkpoint_id}_{node.right.level}_{node.right.position}"
                    if node.right
                    else None
                ),
                snapshot_id=None,
                snapshot_content_hash=None,
                created_at=now,
            )
            self.node_repo.save(node_row)

        logger.info(
            "Merkle checkpoint %s: %d cycles, root=%s",
            checkpoint_id,
            len(sorted_cycles),
            merkle_root[:16],
        )
        return merkle_root

    def get_proof(self, snapshot_id: str) -> MerkleProof | None:
        """Generate an inclusion proof for a snapshot.

        Proves: snapshot → cycle mini-tree → cycle root.
        """
        # Find the leaf node for this snapshot
        leaf_node = self.node_repo.find_by_snapshot_id(snapshot_id)
        if leaf_node is None:
            return None

        cycle_id = leaf_node.cycle_id
        if not cycle_id:
            return None

        # Get all nodes for this cycle's tree
        cycle_nodes_rows = self.node_repo.find_by_cycle_id(cycle_id)

        # Rebuild in-memory tree for proof generation
        nodes_by_id: dict[str, MerkleNode] = {}
        for row in cycle_nodes_rows:
            nodes_by_id[row.id] = MerkleNode(
                hash=row.hash,
                level=row.level,
                position=row.position,
                snapshot_id=row.snapshot_id,
                snapshot_content_hash=row.snapshot_content_hash,
            )

        # Link parent-child relationships
        for row in cycle_nodes_rows:
            node = nodes_by_id[row.id]
            if row.left_child_id and row.left_child_id in nodes_by_id:
                node.left = nodes_by_id[row.left_child_id]
            if row.right_child_id and row.right_child_id in nodes_by_id:
                node.right = nodes_by_id[row.right_child_id]

        all_mem_nodes = list(nodes_by_id.values())
        proof_steps = generate_proof(all_mem_nodes, leaf_node.hash)

        # Get cycle info
        cycle = self.cycle_repo.get(cycle_id)

        # Find checkpoint that covers this cycle
        checkpoint_id = None
        merkle_root = None
        checkpoint_nodes = self.node_repo.find_by_hash_in_checkpoint(
            cycle.chained_root if cycle else leaf_node.hash,
        )
        if checkpoint_nodes:
            checkpoint_id = checkpoint_nodes[0].checkpoint_id

        return MerkleProof(
            snapshot_id=snapshot_id,
            snapshot_content_hash=leaf_node.snapshot_content_hash or leaf_node.hash,
            cycle_id=cycle_id,
            cycle_root=cycle.chained_root if cycle else None,
            checkpoint_id=checkpoint_id,
            merkle_root=merkle_root,
            path=proof_steps,
        )
