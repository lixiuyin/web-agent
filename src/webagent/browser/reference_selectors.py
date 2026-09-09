"""Compact, observation-scoped references and legacy binding compatibility."""

from __future__ import annotations

import re
from typing import Any


def is_observed_selector(selector: dict[str, Any]) -> bool:
    return selector.get("type") == "ref" or bool({"ref", "observation_id"} & selector.keys())


def reference_parts(selector: dict[str, Any]) -> tuple[str, str, str | None]:
    if selector.get("type") == "ref":
        value = selector.get("value", "")
        if not isinstance(value, str) or not re.fullmatch(r"[\w-]+/f\d+:e\d+", value):
            raise ValueError("reference value must be observation_id/fN:eN")
        observation_id, ref = value.split("/", 1)
        legacy_observation = selector.get("observation_id", observation_id)
        legacy_ref = selector.get("ref", ref)
        if legacy_observation != observation_id or legacy_ref != ref:
            raise ValueError("compact references cannot contain conflicting legacy bindings")
        return observation_id, ref, None
    if selector.get("type") != "css" or not all(
        isinstance(selector.get(key), str) and selector[key]
        for key in ("observation_id", "ref", "value")
    ):
        raise ValueError("observation references require css, observation_id and ref")
    return selector["observation_id"], selector["ref"], selector["value"]
