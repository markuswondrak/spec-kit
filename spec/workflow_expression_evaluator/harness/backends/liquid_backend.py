"""Candidate backend: the python-liquid prototype engine (``engine_liquid``).

The prototype replaces the in-block grammar walk with Liquid's own expression
grammars (output statement first, ``{% if %}`` condition as fallback) and keeps
the shared template scanner, namespace, filters and operators. ``loc()`` counts
the engine module itself -- the Liquid environment, the strict filter layer, and
the tree-based probe/diagnostics compatibility layer -- which is the surface S5
measures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specify_cli.workflows.expressions import engine_liquid
from specify_cli.workflows.expressions.namespace import _build_namespace

_MODULE = Path(engine_liquid.__file__)


def _coerce_condition(result: Any) -> bool:
    if isinstance(result, str):
        lowered = result.strip().lower()
        if lowered == "false":
            return False
        if lowered == "true":
            return True
    return bool(result)


class LiquidBackend:
    name = "liquid"

    def evaluate_expression(self, template: Any, context: Any) -> Any:
        if not isinstance(template, str):
            return template
        return engine_liquid._evaluate_template(template, _build_namespace(context))

    def evaluate_condition(self, condition: Any, context: Any) -> bool:
        return _coerce_condition(self.evaluate_expression(condition, context))

    def loc(self) -> int:
        return sum(
            1
            for line in _MODULE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
