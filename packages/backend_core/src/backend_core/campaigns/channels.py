"""Explicit Phase 3A channel enablement seam.

All known channels are architecture values, but no production channel is enabled
by default in Phase 3A.  Later work can provide a configuration-backed registry
without changing Campaign or Outreach business rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend_core.outreach.enums import OutreachChannel


class ChannelEnablementRegistry:
    """Minimal explicit registry used for Target creation and execution validation."""

    def is_enabled(self, channel: OutreachChannel) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class StaticChannelEnablementRegistry(ChannelEnablementRegistry):
    """Deterministic test/deployment registry; never enables unavailable Douyin."""

    enabled_channels: frozenset[OutreachChannel]

    def is_enabled(self, channel: OutreachChannel) -> bool:
        return (
            channel in self.enabled_channels
            and channel is not OutreachChannel.DOUYIN_PRIVATE_MESSAGE
        )
