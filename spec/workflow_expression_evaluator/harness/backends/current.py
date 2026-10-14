"""Reference backend: the previous hand-written engine, pinned explicitly.

After ``engine.py`` is switched to a candidate, the shipped ``evaluate_*``
functions no longer reach ``engine_current``. The harness still needs a fixed
reference to compare against, so this backend binds the old engine directly
instead of going through the package facade. ``loc()`` counts the engine module
itself (not the facade) so the A/B is engine-against-engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specify_cli.workflows.expressions import engine_current
from specify_cli.workflows.expressions.namespace import _build_namespace

_MODULE = Path(engine_current.__file__)


def _coerce_condition(result: Any) -> bool:
    if isinstance(result, str):
        lowered = result.strip().lower()
        if lowered == "false":
            return False
        if lowered == "true":
            return True
    return bool(result)


class CurrentBackend:
    name = "current"

    def evaluate_expression(self, template: Any, context: Any) -> Any:
        if not isinstance(template, str):
            return template
        return engine_current._evaluate_template(template, _build_namespace(context))

    def evaluate_condition(self, condition: Any, context: Any) -> bool:
        return _coerce_condition(self.evaluate_expression(condition, context))

    def loc(self) -> int:
        return sum(
            1
            for line in _MODULE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
