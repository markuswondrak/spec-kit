"""Backend protocol and registry for the expression-evaluator harness.

A backend wraps one evaluator implementation behind the two entry points the
rest of Spec Kit depends on: ``evaluate_expression`` and ``evaluate_condition``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..corpus import Case


@runtime_checkable
class Backend(Protocol):
    name: str

    def evaluate_expression(self, template: Any, context: Any) -> Any: ...

    def evaluate_condition(self, condition: Any, context: Any) -> bool: ...

    def loc(self) -> int:
        """Executable lines of this backend, including its compat shim."""
        ...


def load_backends() -> list[Backend]:
    """Return every backend that is importable in the current environment.

    Candidate backends (lark, jinja2, pyparsing, liquid) live in optional
    modules; a missing dependency simply drops that backend from the run instead
    of failing.
    """
    from .current import CurrentBackend

    backends: list[Backend] = [CurrentBackend()]

    try:
        from .lark_backend import LarkBackend

        backends.append(LarkBackend())
    except Exception:  # noqa: BLE001 - optional prototype backend
        pass

    try:
        from .lark_a2_backend import LarkA2Backend

        backends.append(LarkA2Backend())
    except Exception:  # noqa: BLE001 - optional prototype backend
        pass

    try:
        from .jinja2_backend import Jinja2Backend

        backends.append(Jinja2Backend())
    except Exception:  # noqa: BLE001 - optional prototype backend
        pass

    try:
        from .pyparsing_backend import PyparsingBackend

        backends.append(PyparsingBackend())
    except Exception:  # noqa: BLE001 - optional prototype backend
        pass

    try:
        from .liquid_backend import LiquidBackend

        backends.append(LiquidBackend())
    except Exception:  # noqa: BLE001 - optional prototype backend
        pass

    return backends


__all__ = ["Backend", "Case", "load_backends"]
