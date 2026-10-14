"""Jinja2-based workflow expression engine (Variante B prototype).

This module is an alternative to ``engine_current`` swapped in through the single
import line in ``engine.py``. It fulfills the same ``engine`` contract, but the
in-block grammar walk (``_evaluate_simple_expression``) is replaced by a
``SandboxedEnvironment``:

* a whole ``{{ ... }}`` body goes through ``compile_expression`` and keeps its
  type;
* a mixed template is interpolated block-by-block and yields a string.

Deliberately **not** replaced: the template scanner. The ``{{``/``}}`` block
delimiters, the quote-aware block close, and the ``_first_unclosable_block``
prediction are the *shared* ``template`` module — the same code the diagnostics
reads. Jinja only replaces the grammar *inside* a block. (The plan's Variante B
imagined the scanner would go too; keeping it is a scoping decision that avoids
the ``{#``/``${#}`` collision and the whitespace-control semantics entirely.)

Known behavioral deviations from ``engine_current`` are documented in
``spec/workflow_expression_evaluator/reports/s5-jinja2.md``. The ones that can be
closed through a documented extension point *are* closed here (key-precedence
``getattr``, strict filter arity, ``None`` for missing paths); the operator
semantics (numeric-string comparison, ``and``/``or`` coercion, chained
comparison) cannot be, and are left as Jinja's own behavior.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import Any

import jinja2
from jinja2 import ChainableUndefined, nodes
from jinja2.sandbox import SandboxedEnvironment

from .filters import (
    _filter_contains,
    _filter_default,
    _filter_from_json,
    _filter_join,
    _filter_length,
    _filter_lower,
    _filter_map,
    _filter_split,
    _filter_to_json,
    _filter_upper,
)
from .namespace import _NAMESPACE_ROOTS
from .template import (
    _find_block_close,
    _first_unclosable_block,
    _is_single_expression,
)


# The contract this engine fulfills -- identical to ``engine_current``, so
# ``engine.py`` re-exports the same names whichever engine is selected.
__all__ = [
    "_COMPARISON_OPERATORS",
    "_WORD_OPERATORS",
    "_collect_leaves",
    "_evaluate_template",
    "_evaluator_rejects",
    "_first_unclosable_block",
    "_has_incomplete_operand",
    "_is_single_expression",
    "_leaf_sink",
]


# The language's operator tables. These are the *language*, not the parser, so
# they stay identical to ``engine_current`` and the diagnostics keeps reading the
# same shape through the contract.
_COMPARISON_OPERATORS = ("!=", "==", ">=", "<=", ">", "<", " not in ", " in ")
_WORD_OPERATORS = tuple(
    op for op in (" or ", " and ") + _COMPARISON_OPERATORS if op.startswith(" ")
)

# Contract compatibility. The Jinja2 engine derives its leaves from the parsed
# AST, so nothing appends here, but the diagnostics contract still exposes the
# sink and tests import it.
_leaf_sink: ContextVar[list[str] | None] = ContextVar("_leaf_sink", default=None)


# -- Filters -----------------------------------------------------------------

def _strict(name: str, fn: Any, *, minimum: int, maximum: int) -> Any:
    """Wrap a shared filter so a mis-wired arity fails loudly.

    The shared ``filters.py`` functions are already strict about their *values*;
    this adds the arity check the hand-written dispatcher did, so
    ``| join`` (no separator) and ``| from_json()`` (a call where a bare name is
    required) raise instead of silently running with a default.
    """

    def wrapper(value: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs or not (minimum <= len(args) <= maximum):
            raise ValueError(
                f"{name}: expected an argument count in "
                f"[{minimum}, {maximum}] (got '| {name}' with {len(args)} args)"
            )
        # A missing path arrives as a Jinja Undefined; normalize it to None
        # *before* the shared filter sees it, so ``default`` triggers on a
        # missing value exactly as it does in the current engine.
        if isinstance(value, jinja2.Undefined):
            value = None
        return fn(value, *args)

    wrapper.__name__ = name
    return wrapper


class _ExpressionEnvironment(SandboxedEnvironment):
    """Sandboxed environment with the two extension points the plan calls for.

    * ``getattr`` prefers a mapping key over an object attribute, so
      ``inputs.items`` is the key ``"items"`` and not ``dict.items``.
    * ``undefined=ChainableUndefined`` plus a ``None`` conversion at the
      boundary makes a missing path resolve to ``None`` and not raise.
    """

    def getattr(self, obj: Any, attribute: str) -> Any:
        if isinstance(obj, dict):
            try:
                return obj[attribute]
            except KeyError:
                return self.undefined(obj=obj, name=attribute)
        return super().getattr(obj, attribute)


@lru_cache(maxsize=1)
def _environment() -> _ExpressionEnvironment:
    """Build the environment once, lazily, so CLI start-up stays cheap."""
    env = _ExpressionEnvironment(undefined=ChainableUndefined)
    # Replace Jinja's built-in filter table (truncate, replace, capitalize, ...)
    # with exactly the workflow language's filters. Leaving the defaults in would
    # silently widen the language: `inputs.x | replace('a', 'b')` would just work
    # even though no such filter is documented.
    env.filters = {
        "default": _strict("default", _filter_default, minimum=0, maximum=1),
        "join": _strict("join", _filter_join, minimum=1, maximum=1),
        "map": _strict("map", _filter_map, minimum=1, maximum=1),
        "contains": _strict("contains", _filter_contains, minimum=1, maximum=1),
        "split": _strict("split", _filter_split, minimum=1, maximum=1),
        "from_json": _strict("from_json", _filter_from_json, minimum=0, maximum=0),
        "upper": _strict("upper", _filter_upper, minimum=0, maximum=0),
        "lower": _strict("lower", _filter_lower, minimum=0, maximum=0),
        "length": _strict("length", _filter_length, minimum=0, maximum=0),
        "to_json": _strict("to_json", _filter_to_json, minimum=0, maximum=0),
    }
    return env


# -- Evaluation --------------------------------------------------------------

class _ProbeNamespace(dict):
    """Namespace for the probe: every root exists, every leaf is absent.

    Access to a missing key hands back another ``_ProbeNamespace`` so a probe
    can walk ``inputs.a.b`` without real inputs, mirroring
    ``engine_current._ProbeNamespace``.
    """

    def __missing__(self, key: str) -> "_ProbeNamespace":  # noqa: UP037
        return _ProbeNamespace()


def _normalize(value: Any) -> Any:
    """Map a Jinja ``Undefined`` (missing path) to ``None``."""
    if isinstance(value, jinja2.Undefined):
        return None
    return value


def _eval_expression(expr: str, namespace: dict[str, Any]) -> Any:
    """Compile and run one block body. Raises ``jinja2.TemplateError`` on wiring
    faults and ``ValueError`` from the shared filters' own strictness."""
    if not expr.strip():
        # ``{{ }}`` / ``{{  }}``: an empty body resolves to None, not a parse
        # error (Jinja raises "unexpected end of template" on an empty source).
        return None
    compiled = _environment().compile_expression(expr)
    return _normalize(compiled(**namespace))


def _interpolate(template: str, namespace: dict[str, Any]) -> str:
    """Substitute every top-level ``{{ ... }}`` block, quote-aware.

    Reuses ``template._find_block_close`` (the shared scanner) so a literal
    ``}}`` inside a string argument does not close a block early, and keeps the
    raw-close fallback for a template the quote-aware scan cannot close —
    identical routing to ``engine_current._interpolate_expressions``.
    """
    out: list[str] = []
    i = 0
    n = len(template)
    while i < n:
        start = template.find("{{", i)
        if start == -1:
            out.append(template[i:])
            break
        out.append(template[i:start])
        close = _find_block_close(template, start)
        if close == -1:
            raw_close = template.find("}}", start + 2)
            if raw_close == -1:
                out.append(template[start:])
                break
            close = raw_close
        val = _eval_expression(template[start + 2:close].strip(), namespace)
        out.append(str(val) if val is not None else "")
        i = close + 2
    return "".join(out)


def _evaluate_template(template: str, namespace: dict[str, Any]) -> Any:
    """Evaluate *template* against an already-built *namespace*.

    Single ``{{ ... }}`` block: typed value via ``compile_expression``. Anything
    else: interpolated string. The single vs. mixed decision and the block
    scanner are the shared ``template`` primitives, so the diagnostics and the
    evaluator cannot disagree about where a block ends.
    """
    stripped = template.strip()
    try:
        if _is_single_expression(stripped):
            return _eval_expression(stripped[2:-2].strip(), namespace)
        return _interpolate(template, namespace)
    except jinja2.TemplateError as exc:
        # Normalize to the ValueError the current engine raises, so callers and
        # the harness see the same error *type*.
        raise ValueError(str(exc)) from exc


# -- Probe / diagnostics -----------------------------------------------------

def _reconstruct(node: Any) -> str | None:
    """Rebuild a dotted path from a ``Getattr``/``Getitem`` chain, or ``None``.

    ``inputs.a.b`` -> ``"inputs.a.b"``; ``items[0]`` -> ``"items[0]"``. A dynamic
    index (``tags[foo]``) has no static path and returns ``None`` so the walker
    descends into it instead.
    """
    parts: list[str] = []
    cur = node
    while True:
        if isinstance(cur, nodes.Getattr):
            parts.append("." + cur.attr)
            cur = cur.node
        elif isinstance(cur, nodes.Getitem):
            arg = cur.arg
            if not isinstance(arg, nodes.Const):
                return None
            parts.append(f"[{arg.value}]")
            cur = cur.node
        elif isinstance(cur, nodes.Name):
            parts.append(cur.name)
            return "".join(reversed(parts))
        else:
            return None


def _collect_leaves(text: str) -> list[str]:
    """Every substring *text* hands to the evaluator as a name to resolve.

    Walks the Jinja AST: each ``Name``, and each ``Getattr``/``Getitem`` chain
    rooted at a ``Name``, is a leaf. A text that does not parse is returned
    whole, so the gate reports the shape rather than staying silent.
    """
    src = text.strip()
    if not src:
        return []
    try:
        ast = _environment().parse("{{ " + src + " }}")
    except jinja2.TemplateError:
        return [src]

    leaves: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, (nodes.Getattr, nodes.Getitem)):
            path = _reconstruct(node)
            if path is not None:
                leaves.append(path)
                return
            # A dynamic index: descend into the index operand.
            if isinstance(node, nodes.Getitem):
                walk(node.arg)
            walk(node.node)
            return
        if isinstance(node, nodes.Name):
            leaves.append(node.name)
            return
        for child in node.iter_child_nodes():
            walk(child)

    walk(ast)
    return leaves


def _evaluator_rejects(text: str) -> str | None:
    """The evaluator's own complaint about how *text* is wired, or ``None``.

    Reports only what is about the author's text: an unknown filter (a compile
    error) and a registered filter used in an unsupported form (a ``ValueError``
    from the strict wrapper, which quotes the segment back). Value errors about
    the probe's own placeholders are not rejections.
    """
    try:
        _eval_expression(text, {root: _ProbeNamespace() for root in _NAMESPACE_ROOTS})
    except jinja2.TemplateSyntaxError as exc:
        return str(exc).split(":", 1)[0]
    except ValueError as exc:
        message = str(exc)
        if "got '| " in message:
            return message.split(":", 1)[0]
        return None
    except Exception:  # noqa: BLE001 - probe values, not the author's text
        return None
    return None


def _has_incomplete_operand(text: str) -> bool:
    """True when *text* does not parse as a complete Jinja expression.

    An operator missing an operand (``inputs.a ==``, ``a and``, ``in inputs.a``)
    is a parse error, which is the Jinja-native equivalent of the current
    engine's operator scan.
    """
    stripped = text.strip()
    if not stripped:
        return True
    try:
        _environment().parse("{{ " + stripped + " }}")
    except jinja2.TemplateError:
        return True
    return False
