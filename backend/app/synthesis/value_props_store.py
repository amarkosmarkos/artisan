"""Value proposition persistence helpers.

Stored payload may be:
- legacy: a single ValueProposition object (flat dict with customer/pain/...)
- current: { "value_propositions": [...], "primary_id": "vp_..." }

The current generator stores the broad company-level VP first. Treat that first
item as primary/fallback instead of picking the highest-confidence narrow VP.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..schemas import ValueProposition


def _ensure_vp_id(vp: ValueProposition) -> ValueProposition:
    if vp.id:
        return vp
    return vp.model_copy(update={"id": f"vp_{uuid.uuid4().hex[:10]}"})


def parse_stored_value_props(raw: Any) -> list[ValueProposition]:
    """Load value propositions from a DB JSON payload with backward compatibility."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return []

    if "value_propositions" in raw:
        vps = [
            _ensure_vp_id(ValueProposition.model_validate(item))
            for item in raw["value_propositions"]
        ]
        primary_id = raw.get("primary_id")
        if primary_id:
            for i, vp in enumerate(vps):
                if vp.id == primary_id:
                    return vps if i == 0 else [vps[i], *vps[:i], *vps[i + 1 :]]
        return vps

    # Legacy single VP object.
    if any(k in raw for k in ("customer", "pain", "outcome", "mechanism")):
        return [_ensure_vp_id(ValueProposition.model_validate(raw))]
    return []


def primary_value_proposition(vps: list[ValueProposition]) -> ValueProposition:
    """Pick the primary VP: the first item is the broad company fallback."""
    if not vps:
        return ValueProposition()
    return vps[0]


def serialize_value_props(vps: list[ValueProposition]) -> dict[str, Any]:
    """Serialize for the value_props table."""
    normalized = [_ensure_vp_id(vp) for vp in vps]
    primary = primary_value_proposition(normalized)
    return {
        "value_propositions": [vp.model_dump(mode="json") for vp in normalized],
        "primary_id": primary.id,
    }


def resolve_value_proposition(
    vps: list[ValueProposition],
    selected_id: str | None,
) -> ValueProposition:
    if not vps:
        return ValueProposition()
    if selected_id:
        for vp in vps:
            if vp.id == selected_id:
                return vp
    return primary_value_proposition(vps)
