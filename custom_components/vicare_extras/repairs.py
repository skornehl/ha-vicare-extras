"""Repair flow: hand the entity IDs of taken-over Extras entities to the core.

When the core ViCare integration takes over a value, Extras removes its own
entity at the next start (core_overlap.py) and raises the repair issue
`core_takeover`. Its fix gives each core entity the entity ID the Extras
entity had, so automations, scripts and dashboards keep working, and the
history of that entity ID goes on. Settings the user gave the Extras entity
(name, icon, area, labels) go to the core entity where it has none.

Nothing is renamed without the user's confirmation: the core entity ID may
be one the user chose, and automations may already use it.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.automation import automations_with_entity
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.components.script import scripts_with_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import DOMAIN, VICARE_DOMAIN
from .core_overlap import (
    ISSUE_CORE_TAKEOVER,
    async_create_takeover_issue,
    format_moves,
    issue_moves,
)

NONE = "-"


def core_entity_id(hass: HomeAssistant, move: dict[str, Any]) -> str | None:
    """Return the current entity ID of the core entity of a move."""
    return er.async_get(hass).async_get_entity_id(move["domain"], VICARE_DOMAIN, move["unique_id"])


def references(hass: HomeAssistant, entity_ids: list[str]) -> str:
    """Return the automations and scripts that use any of the entity IDs."""
    found: set[str] = set()
    for entity_id in entity_ids:
        found.update(automations_with_entity(hass, entity_id))
        found.update(scripts_with_entity(hass, entity_id))
    return ", ".join(f"`{entity_id}`" for entity_id in sorted(found)) or NONE


def async_apply_moves(
    hass: HomeAssistant, moves: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Give each core entity the old Extras entity ID.

    Returns the moved entity IDs and the moves that could not be done: the
    core entity is gone, or something else uses the old entity ID.
    """
    registry = er.async_get(hass)
    moved: list[str] = []
    skipped: list[dict[str, Any]] = []
    for move in moves:
        old = move["old"]
        current = core_entity_id(hass, move)
        if current == old:
            moved.append(old)
            continue
        if current is None or registry.async_is_registered(old) or hass.states.get(old):
            skipped.append(move)
            continue
        core = registry.async_get(current)
        changes: dict[str, Any] = {"new_entity_id": old}
        for setting in ("name", "icon", "area_id"):
            if move.get(setting) and getattr(core, setting) is None:
                changes[setting] = move[setting]
        if move.get("labels"):
            changes["labels"] = set(core.labels) | set(move["labels"])
        registry.async_update_entity(current, **changes)
        moved.append(old)
    return moved, skipped


class CoreTakeoverRepairFlow(RepairsFlow):
    """Confirm, then rename the core entities to the old Extras entity IDs."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, ISSUE_CORE_TAKEOVER)
        moves = issue_moves(issue.data if issue else None)
        if not moves:
            return self.async_abort(reason="nothing_to_move")
        if user_input is not None:
            _, skipped = async_apply_moves(self.hass, moves)
            if not skipped:
                return self.async_create_entry(data={})
            # Keep the issue for what is left, with the reason in the flow.
            async_create_takeover_issue(self.hass, skipped)
            return self.async_abort(
                reason="not_all_moved",
                description_placeholders={"skipped": format_moves(skipped)},
            )
        current = {move["old"]: core_entity_id(self.hass, move) for move in moves}
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "moves": format_moves(moves, current),
                "old_used_by": references(self.hass, list(current)),
                "core_used_by": references(
                    self.hass,
                    [entity_id for old, entity_id in current.items() if entity_id and entity_id != old],
                ),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow of a repair issue of ViCare Extras."""
    return CoreTakeoverRepairFlow()
