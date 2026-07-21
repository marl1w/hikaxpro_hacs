"""Persistent storage for the zone bypass feature.

Keeps the per-zone "bypassable on arming" flags and the tracking
of bypasses owned by the integration across Home Assistant
restarts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import ARM_MODES, ARM_MODE_AWAY, ARM_MODE_HOME, DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2
SAVE_DELAY = 1.0


def _empty_bypassable_by_mode() -> dict[str, set[int]]:
    return {mode: set() for mode in ARM_MODES}


@dataclass
class OwnedBypass:
    """A bypass applied by this integration."""

    applied_at: datetime
    reason: str
    area: int | None = None
    arm_flow_id: str | None = None
    pending_unbypass: bool = False

    def as_dict(self) -> dict:
        """Serialize for storage."""
        return {
            "applied_at": self.applied_at.isoformat(),
            "reason": self.reason,
            "area": self.area,
            "arm_flow_id": self.arm_flow_id,
            "pending_unbypass": self.pending_unbypass,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OwnedBypass:
        """Deserialize from storage."""
        applied_at = dt_util.parse_datetime(data.get("applied_at") or "")
        return cls(
            applied_at=applied_at or dt_util.utcnow(),
            reason=data.get("reason", "unknown"),
            area=data.get("area"),
            arm_flow_id=data.get("arm_flow_id"),
            pending_unbypass=bool(data.get("pending_unbypass", False)),
        )


@dataclass
class BypassData:
    """In-memory view of the stored bypass state."""

    bypassable_zones_by_mode: dict[str, set[int]] = field(
        default_factory=_empty_bypassable_by_mode
    )
    owned_bypasses: dict[int, OwnedBypass] = field(default_factory=dict)
    last_auto_bypass: datetime | None = None


class _BypassHAStore(Store[dict]):
    """Home Assistant Store with schema migration for bypass data."""

    async def _async_migrate_func(
        self, old_major_version: int, _old_minor_version: int, old_data: dict
    ) -> dict:
        """Migrate persisted storage to the current schema version."""
        if old_major_version != 1:
            raise NotImplementedError

        legacy = {
            str(zone_id): bool(flag)
            for zone_id, flag in (old_data.get("bypassable_zones") or {}).items()
            if flag
        }
        return {
            **old_data,
            "bypassable_zones_by_mode": {
                mode: dict(legacy) if mode == ARM_MODE_HOME else {}
                for mode in ARM_MODES
            },
        }


class BypassStore:
    """Typed wrapper over a Home Assistant Store."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store for a config entry."""
        self._store: Store = _BypassHAStore(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.bypass",
        )
        self.data = BypassData()

    async def async_load(self) -> None:
        """Load stored data, tolerating a missing or corrupt file."""
        raw = await self._store.async_load()
        if not raw:
            return
        try:
            loaded_by_mode = _empty_bypassable_by_mode()
            raw_by_mode = raw.get("bypassable_zones_by_mode")
            if isinstance(raw_by_mode, dict):
                for mode in ARM_MODES:
                    loaded_by_mode[mode] = {
                        int(zone_id)
                        for zone_id, flag in (raw_by_mode.get(mode) or {}).items()
                        if flag
                    }
            else:
                # Backwards compatibility with v1 storage where a single
                # mode-less flag set was used. Map it to home only.
                legacy = {
                    int(zone_id)
                    for zone_id, flag in (raw.get("bypassable_zones") or {}).items()
                    if flag
                }
                loaded_by_mode[ARM_MODE_HOME] = set(legacy)
            self.data.bypassable_zones_by_mode = loaded_by_mode
            self.data.owned_bypasses = {
                int(zone_id): OwnedBypass.from_dict(owned)
                for zone_id, owned in (raw.get("owned_bypasses") or {}).items()
            }
            last = raw.get("last_auto_bypass")
            self.data.last_auto_bypass = dt_util.parse_datetime(last) if last else None
        except (TypeError, ValueError) as err:
            _LOGGER.warning("Discarding corrupt bypass storage: %s", err)
            self.data = BypassData()

    def _as_dict(self) -> dict:
        return {
            "bypassable_zones_by_mode": {
                mode: {
                    str(zone_id): True
                    for zone_id in sorted(
                        self.data.bypassable_zones_by_mode.get(mode, set())
                    )
                }
                for mode in ARM_MODES
            },
            # Keep a legacy away-mode projection for smooth downgrades.
            "bypassable_zones": {
                str(zone_id): True
                for zone_id in sorted(
                    self.data.bypassable_zones_by_mode.get(ARM_MODE_AWAY, set())
                )
            },
            "owned_bypasses": {
                str(zone_id): owned.as_dict()
                for zone_id, owned in self.data.owned_bypasses.items()
            },
            "last_auto_bypass": self.data.last_auto_bypass.isoformat()
            if self.data.last_auto_bypass
            else None,
        }

    async def async_save(self) -> None:
        """Save immediately (used as write-ahead before bypass commands)."""
        await self._store.async_save(self._as_dict())

    def async_delay_save(self) -> None:
        """Schedule a delayed save for non-critical mutations."""
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)
