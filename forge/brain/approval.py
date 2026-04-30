from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge.runtime.state_store import PersistentStateStore


AUTO_APPROVE_CLASSES = {"local_readonly"}
HUMAN_APPROVAL_CLASSES = {
    "network_post",
    "network_egress",
    "authenticated_browser",
    "external_publish",
    "sensitive_file_write",
    "destructive_shell",
}


@dataclass(slots=True)
class ApprovalDecision:
    allowed: bool
    approval_required: bool
    approval_id: str | None
    approval_class: str | None
    notes: list[str]
    status: str


class ApprovalPolicyEngine:
    """Policy + encrypted payload approval flow."""

    def __init__(self, store: PersistentStateStore) -> None:
        self._store = store

    def evaluate(
        self,
        *,
        mission_id: str,
        step_id: str,
        approval_class: str | None,
        request_excerpt: str,
        payload: dict[str, Any],
        summary: str,
        confirmed: bool,
    ) -> ApprovalDecision:
        if not approval_class:
            return ApprovalDecision(True, False, None, None, [], "allowed")

        if approval_class in AUTO_APPROVE_CLASSES:
            return ApprovalDecision(
                True,
                False,
                None,
                approval_class,
                [f"Approval class `{approval_class}` auto-approved by policy."],
                "auto_approved",
            )

        if confirmed:
            return ApprovalDecision(
                True,
                False,
                None,
                approval_class,
                [f"Approval class `{approval_class}` approved explicitly by operator input."],
                "confirmed",
            )

        if approval_class in HUMAN_APPROVAL_CLASSES:
            approval_id = self._store.create_pending_approval(
                mission_id=mission_id,
                step_id=step_id,
                approval_class=approval_class,
                request_excerpt=request_excerpt[:500],
                payload=payload,
                summary=summary,
                policy_mode="human_approval",
            )
            return ApprovalDecision(
                False,
                True,
                approval_id,
                approval_class,
                [
                    f"Approval class: {approval_class}. Human confirmation is required before execution.",
                    f"Approval request created: {approval_id}.",
                ],
                "pending",
            )

        return ApprovalDecision(
            True,
            False,
            None,
            approval_class,
            [f"Approval class `{approval_class}` allowed by current policy."],
            "allowed",
        )

    def approval_status(self, approval_id: str) -> dict[str, Any] | None:
        return self._store.get_approval(approval_id, include_payload=False)

    def approve(self, approval_id: str, *, notes: str = "") -> dict[str, Any] | None:
        return self._store.decide_approval(approval_id, approved=True, notes=notes)

    def reject(self, approval_id: str, *, notes: str = "") -> dict[str, Any] | None:
        return self._store.decide_approval(approval_id, approved=False, notes=notes)

    def list_pending(self) -> list[dict[str, Any]]:
        return self._store.list_approvals(status="pending")
