from __future__ import annotations

from threading import Event


class OperationCancelledError(RuntimeError):
    """Raised when the user cancels a background backup or restore task."""


def raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise OperationCancelledError("Operation cancelled by user")
