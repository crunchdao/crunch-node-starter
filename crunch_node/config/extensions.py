from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtensionSettings:
    scoring_function: str

    @classmethod
    def from_env(cls) -> ExtensionSettings:
        return cls(
            scoring_function=os.getenv(
                "SCORING_FUNCTION",
                "crunch_node.extensions.default_callables:default_score_prediction",
            ),
        )
