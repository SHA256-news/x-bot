"""State management for simplebot with bootstrap support."""

import json
from pathlib import Path
from typing import Any, Dict, MutableMapping


def load_state(path: Path) -> Dict[str, Any]:
    """Load persisted state from path with simplebot defaults.
    
    The state dictionary provides all required keys including the bootstrapCompleted flag.
    """
    if not path.exists():
        return {
            "updatesAfterNewsUri": None,
            "updatesAfterBlogUri": None,
            "updatesAfterPrUri": None,
            "postedArticleUris": [],
            "bootstrapCompleted": False,
        }

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to load bot state from {path}: {exc}") from exc

    # Ensure all required keys are present
    data.setdefault("updatesAfterNewsUri", None)
    data.setdefault("updatesAfterBlogUri", None)
    data.setdefault("updatesAfterPrUri", None)
    data.setdefault("postedArticleUris", [])
    data.setdefault("bootstrapCompleted", False)
    return data


def save_state(path: Path, state: MutableMapping[str, Any]) -> None:
    """Persist the state dictionary to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)