"""Explicit state machine for a user's biometric enrollment status.

    not_enrolled ──start──▶ in_progress ──succeed──▶ active ──suspend──▶ suspended
          ▲                   │   │                    │  ▲                │
          │                   │   └─fail_retryable─▶ pending_retry ─start─┘  └──reinstate──┘
          └────reset──── verification_failed ◀─fail_final─┘
    any (except deleted) ──delete──▶ deleted ──start──▶ in_progress

Re-enrollment by an *active* user keeps the status ``active`` throughout; the old template stays in
force until the new one succeeds, and the new face must match the old template (see enrollment).
"""

from __future__ import annotations

import enum

from app.models.enums import BiometricStatus as S


class BiometricEvent(enum.StrEnum):
    START = "start"
    SUCCEED = "succeed"
    FAIL_RETRYABLE = "fail_retryable"
    FAIL_FINAL = "fail_final"
    SUSPEND = "suspend"
    REINSTATE = "reinstate"
    RESET = "reset"
    DELETE = "delete"


class InvalidTransition(ValueError):
    def __init__(self, current: S, event: BiometricEvent) -> None:
        super().__init__(f"cannot apply '{event.value}' to biometric status '{current.value}'")
        self.current = current
        self.event = event


E = BiometricEvent

TRANSITIONS: dict[tuple[S, BiometricEvent], S] = {
    (S.NOT_ENROLLED, E.START): S.IN_PROGRESS,
    (S.PENDING_RETRY, E.START): S.IN_PROGRESS,
    (S.DELETED, E.START): S.IN_PROGRESS,
    (S.IN_PROGRESS, E.START): S.IN_PROGRESS,
    (S.ACTIVE, E.START): S.ACTIVE,
    (S.IN_PROGRESS, E.SUCCEED): S.ACTIVE,
    (S.ACTIVE, E.SUCCEED): S.ACTIVE,
    (S.IN_PROGRESS, E.FAIL_RETRYABLE): S.PENDING_RETRY,
    (S.ACTIVE, E.FAIL_RETRYABLE): S.ACTIVE,
    (S.IN_PROGRESS, E.FAIL_FINAL): S.VERIFICATION_FAILED,
    (S.ACTIVE, E.FAIL_FINAL): S.ACTIVE,
    (S.ACTIVE, E.SUSPEND): S.SUSPENDED,
    (S.SUSPENDED, E.REINSTATE): S.ACTIVE,
    (S.VERIFICATION_FAILED, E.RESET): S.NOT_ENROLLED,
    (S.PENDING_RETRY, E.RESET): S.NOT_ENROLLED,
    (S.IN_PROGRESS, E.RESET): S.NOT_ENROLLED,
    **{(s, E.DELETE): S.DELETED for s in S if s != S.DELETED},
}


def next_status(current: S, event: BiometricEvent) -> S:
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransition(current, event) from None


def can(current: S, event: BiometricEvent) -> bool:
    return (current, event) in TRANSITIONS
