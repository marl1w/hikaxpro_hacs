"""Helpers for valid Home Assistant object/entity IDs."""

from __future__ import annotations

from hashlib import sha1
import re

from homeassistant.util import slugify

INVALID_OBJECT_ID_RE = re.compile(r"[^a-z0-9_]")


def normalized_object_id(value: str | None, fallback: str | None = None) -> str:
    """Return a valid HA object_id slug.

    Uses HA slugify first. If that produces an empty value, falls back to a
    slugified fallback, then finally to a deterministic hash-based identifier.
    """
    slug = slugify(value or "")
    if slug:
        return slug

    fallback_slug = slugify(fallback or "")
    if fallback_slug:
        return fallback_slug

    seed = (fallback or value or "entity").encode("utf-8")
    return f"entity_{sha1(seed).hexdigest()[:10]}"


def has_invalid_object_id_chars(entity_id: str) -> bool:
    """Return True when object_id contains chars outside [a-z0-9_]."""
    if "." not in entity_id:
        return True
    _, object_id = entity_id.split(".", 1)
    return bool(INVALID_OBJECT_ID_RE.search(object_id))


def object_id_name_remainder(
    device_name: str | None, entity_name: str | None
) -> str | None:
    """Entity-name part of the object id, minus the device-name overlap.

    Home Assistant composes the object id of a ``has_entity_name``
    entity as ``<device name> <this value>``. Stripping the words the
    entity name shares with the tail of the device name avoids
    duplicated ids: device "Villa 1" + entity "Villa 1 Alarm Panel"
    yields ``villa_1_alarm_panel``, not ``villa_1_villa_1_alarm_panel``.
    Returns None when the entity name is fully covered by the device
    name (the id is then the device name alone).
    """
    entity_tokens = [t for t in slugify(entity_name or "").split("_") if t]
    if not entity_tokens:
        return None
    device_tokens = [t for t in slugify(device_name or "").split("_") if t]
    overlap = 0
    for k in range(min(len(device_tokens), len(entity_tokens)), 0, -1):
        if device_tokens[-k:] == entity_tokens[:k]:
            overlap = k
            break
    if overlap == 0:
        return entity_name
    remainder = entity_tokens[overlap:]
    if not remainder:
        return None
    return " ".join(remainder)


def collapse_duplicate_token_runs(object_id: str) -> str:
    """Collapse adjacent duplicated multi-word runs in an object id.

    ``villa_1_villa_1_alarm_panel`` -> ``villa_1_alarm_panel``. Only
    runs of two or more words are collapsed: single repeated words
    (``garage_garage``) and numeric collision suffixes (``bypass_2_2``)
    are too ambiguous to rewrite safely.
    """
    tokens = [t for t in object_id.split("_") if t]
    changed = True
    while changed:
        changed = False
        n = len(tokens)
        for k in range(n // 2, 1, -1):
            for i in range(n - 2 * k + 1):
                if tokens[i : i + k] == tokens[i + k : i + 2 * k]:
                    tokens = tokens[: i + k] + tokens[i + 2 * k :]
                    changed = True
                    break
            if changed:
                break
    return "_".join(tokens)
