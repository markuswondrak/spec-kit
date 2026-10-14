"""Candidate backend: the A2 engine (``engine_lark_a2``) — Lark without emulation.

Unlike the A/C prototypes, this engine uses Lark the way the library is
designed: an LALR grammar (guards included in the grammar, path terminal
derived from ``namespace``), a table-driven filter dispatcher, and static
contract primitives (``_collect_leaves`` walks the tree, ``_evaluator_rejects``
checks the filter table, ``_syntax_error`` reports parse failures). ``loc()``
counts the engine module, which is the whole A2 surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specify_cli.workflows.expressions import engine_lark_a2
from specify_cli.workflows.expressions.namespace import _build_namespace

_MODULE = Path(engine_lark_a2.__file__)


def _coerce_condition(result: Any) -> bool:
    if isinstance(result, str):
        lowered = result.strip().lower()
        if lowered == "false":
            return False
        if lowered == "true":
            return True
    return bool(result)


class LarkA2Backend:
    name = "lark-a2"

    def evaluate_expression(self, template: Any, context: Any) -> Any:
        if not isinstance(template, str):
            return template
        return engine_lark_a2._evaluate_template(template, _build_namespace(context))

    def evaluate_condition(self, condition: Any, context: Any) -> bool:
        return _coerce_condition(self.evaluate_expression(condition, context))

    def loc(self) -> int:
        return sum(
            1
            for line in _MODULE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
