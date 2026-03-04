"""Binary Merkle tree construction and proof generation."""

from __future__ import annotations

from dataclasses import dataclass, field

from crunch_node.merkle.hasher import sha256_concat


@dataclass
class MerkleNode:
    """In-memory node used during tree construction."""

    hash: str
    level: int
    position: int
    left: MerkleNode | None = None
    right: MerkleNode | None = None
    snapshot_id: str | None = None
    snapshot_content_hash: str | None = None


@dataclass
class ProofStep:
    hash: str
    position: str  # "left" or "right" — position of the sibling


@dataclass
class MerkleProof:
    snapshot_id: str
    snapshot_content_hash: str
    cycle_id: str | None = None
    cycle_root: str | None = None
    checkpoint_id: str | None = None
    merkle_root: str | None = None
    path: list[ProofStep] = field(default_factory=list)


def build_merkle_tree(leaves: list[MerkleNode]) -> list[MerkleNode]:
    """Build a binary Merkle tree from leaf nodes.

    Returns a flat list of all nodes (leaves + intermediates + root).
    The last element is the root.

    If there are no leaves, returns an empty list.
    If odd number of nodes at a level, the last node is duplicated.
    """
    if not leaves:
        return []

    if len(leaves) == 1:
        return list(leaves)

    all_nodes: list[MerkleNode] = list(leaves)
    current_level = leaves

    level = 1
    while len(current_level) > 1:
        next_level: list[MerkleNode] = []
        # Pad odd levels by duplicating the last node
        if len(current_level) % 2 == 1:
            current_level.append(current_level[-1])

        for i in range(0, len(current_level), 2):
            left = current_level[i]
            right = current_level[i + 1]
            parent_hash = sha256_concat(left.hash, right.hash)
            parent = MerkleNode(
                hash=parent_hash,
                level=level,
                position=i // 2,
                left=left,
                right=right,
            )
            next_level.append(parent)
            all_nodes.append(parent)

        current_level = next_level
        level += 1

    return all_nodes


def get_root(nodes: list[MerkleNode]) -> MerkleNode | None:
    """Get the root node (highest level) from a flat node list."""
    if not nodes:
        return None
    return max(nodes, key=lambda n: n.level)


def generate_proof(nodes: list[MerkleNode], leaf_hash: str) -> list[ProofStep]:
    """Generate an inclusion proof for a leaf hash.

    Returns the list of sibling hashes needed to recompute the root.
    """
    # Find the leaf
    leaf = None
    for node in nodes:
        if node.level == 0 and node.hash == leaf_hash:
            leaf = node
            break

    if leaf is None:
        return []

    # Build parent lookup
    parent_map: dict[id, MerkleNode] = {}
    for node in nodes:
        if node.left is not None:
            parent_map[id(node.left)] = node
        if node.right is not None:
            parent_map[id(node.right)] = node

    # Walk up to root
    path: list[ProofStep] = []
    current = leaf
    while id(current) in parent_map:
        parent = parent_map[id(current)]
        if parent.left is current:
            # Sibling is on the right
            if parent.right is not None:
                path.append(ProofStep(hash=parent.right.hash, position="right"))
        else:
            # Sibling is on the left
            if parent.left is not None:
                path.append(ProofStep(hash=parent.left.hash, position="left"))
        current = parent

    return path


def verify_proof(leaf_hash: str, proof: list[ProofStep], expected_root: str) -> bool:
    """Verify a Merkle inclusion proof."""
    current = leaf_hash
    for step in proof:
        if step.position == "right":
            current = sha256_concat(current, step.hash)
        else:
            current = sha256_concat(step.hash, current)
    return current == expected_root
