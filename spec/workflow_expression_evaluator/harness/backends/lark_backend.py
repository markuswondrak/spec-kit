"""Candidate backend: the Lark prototype engine (``engine_lark``).

The prototype reuses the shared leaves (namespace, filters, operators, template)
and replaces only the in-block parser with a Lark grammar. ``loc()`` counts the
engine module itself, which is the grammar plus the dispatcher/probe
compatibility layer -- the surface S5 measures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specify_cli.workflows.expressions import engine_lark
from specify_cli.workflows.expressions.namespace import _build_namespace

_MODULE = Path(engine_lark.__file__)


def _coerce_condition(result: Any) -> bool:
    if isinstance(result, str):
        lowered = result.strip().lower()
        if lowered == "false":
            return False
        if lowered == "true":
            return True
    return bool(result)


class LarkBackend:
    name = "lark"

    def evaluate_expression(self, template: Any, context: Any) -> Any:
        if not isinstance(template, str):
            return template
        return engine_lark._evaluate_template(template, _build_namespace(context))

    def evaluate_condition(self, condition: Any, context: Any) -> bool:
        return _coerce_condition(self.evaluate_expression(condition, context))

    def loc(self) -> int:
        return sum(
            1
            for line in _MODULE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
