"""python-liquid-based workflow expression engine (Variante D prototype).

This module is an alternative to ``engine_current`` swapped in through the single
import line in ``engine.py``. It fulfills the same ``engine`` contract, but the
in-block grammar walk (``_evaluate_simple_expression``) is replaced by a
``liquid.Environment``:

* a whole ``{{ ... }}`` body goes through Liquid's output-expression grammar
  (``FilteredExpression``) and keeps its type;
* an expression the output grammar rejects -- Liquid only allows comparisons and
  logical operators inside ``{% if %}`` tags -- is retried as a tag condition
  (``BooleanExpression``), so ``{{ inputs.count > inputs.limit }}`` still yields
  a typed bool;
* a mixed template is interpolated block-by-block and yields a string.

Deliberately **not** replaced: the template scanner. The ``{{``/``}}`` block
delimiters, the quote-aware block close, and the ``_first_unclosable_block``
prediction are the *shared* ``template`` module. Liquid replaces the grammar
*inside* a block, exactly like the Jinja2 and Lark prototypes.

Liquid specifics this engine confronts:

* ``logical_not_operator`` and ``logical_parentheses`` are set on the
  environment subclass; both are documented class options of ``Environment``.
* Liquid's filter *call* syntax is ``| name: arg``; the workflow language's
  ``| name(arg)`` therefore fails to parse. This is left as a documented
  deviation, not rewritten.
* Reserved words (``limit``, ``empty``, ``in``, ``for``, ...) cannot be path
  segments; ``inputs.limit`` is a parse error in Liquid.
* ``Undefined`` is normalized to ``None`` at the boundary, so a missing path
  resolves to ``None`` like the current engine.

Known behavioral deviations from ``engine_current`` are documented in
``spec/workflow_expression_evaluator/reports/s5-liquid.md``. The ones that can be
closed through a documented extension point *are* closed here (logical ``not``
and parentheses via the environment options, strict filter arity, ``None`` for
missing paths); the filter call syntax, the reserved words and the comparison
semantics cannot be, and are left as Liquid's own behavior.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import Any

import liquid
from liquid import Environment
from liquid.builtin.expressions import Path
from liquid.context import RenderContext
from liquid.exceptions import LiquidError, LiquidSyntaxError

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

# Contract compatibility. The Liquid engine derives its leaves from the parsed
# expression tree, so nothing appends here, but the diagnostics contract still
# exposes the sink and tests import it.
_leaf_sink: ContextVar[list[str] | None] = ContextVar("_leaf_sink", default=None)


# -- Filters -----------------------------------------------------------------

def _strict(name: str, fn: Any, *, minimum: int, maximum: int) -> Any:
    """Wrap a shared filter so a mis-wired arity fails loudly.

    The shared ``filters.py`` functions are already strict about their *values*;
    this adds the arity check the hand-written dispatcher did, so ``| join``
    (no separator) raises instead of silently running with a default. Liquid
    passes a missing path as ``Undefined``; it is normalized to ``None`` before
    the shared filter sees it, so ``default`` triggers exactly as it does in the
    current engine.
    """

    def wrapper(value: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs or not (minimum <= len(args) <= maximum):
            raise ValueError(
                f"{name}: expected an argument count in "
                f"[{minimum}, {maximum}] (got '| {name}' with {len(args)} args)"
            )
        if liquid.is_undefined(value):
            value = None
        return fn(value, *args)

    wrapper.__name__ = name
    return wrapper


class _LiquidEnvironment(Environment):
    """Environment with the two documented class options this language needs.

    ``logical_not_operator`` enables ``not``; ``logical_parentheses`` enables
    grouping parentheses. Both default to ``False`` for Shopify compatibility
    and are meant to be enabled on a subclass.
    """

    logical_not_operator = True
    logical_parentheses = True


@lru_cache(maxsize=1)
def _environment() -> _LiquidEnvironment:
    """Build the environment once, lazily, so CLI start-up stays cheap."""
    env = _LiquidEnvironment()
    # Replace Liquid's built-in filter table (where, slice, truncate, ...) with
    # exactly the workflow language's filters. Leaving the defaults in would
    # silently widen the language: `inputs.x | slice: 2` would just work even
    # though no such filter is documented.
    env.filters.clear()
    env.add_filter("default", _strict("default", _filter_default, minimum=0, maximum=1))
    env.add_filter("join", _strict("join", _filter_join, minimum=1, maximum=1))
    env.add_filter("map", _strict("map", _filter_map, minimum=1, maximum=1))
    env.add_filter("contains", _strict("contains", _filter_contains, minimum=1, maximum=1))
    env.add_filter("split", _strict("split", _filter_split, minimum=1, maximum=1))
    env.add_filter("from_json", _strict("from_json", _filter_from_json, minimum=0, maximum=0))
    env.add_filter("upper", _strict("upper", _filter_upper, minimum=0, maximum=0))
    env.add_filter("lower", _strict("lower", _filter_lower, minimum=0, maximum=0))
    env.add_filter("length", _strict("length", _filter_length, minimum=0, maximum=0))
    env.add_filter("to_json", _strict("to_json", _filter_to_json, minimum=0, maximum=0))
    return env


# -- Evaluation --------------------------------------------------------------

@lru_cache(maxsize=1)
def _empty_template() -> Any:
    """A dummy template for ``RenderContext``; not rendered, only carried."""
    return _environment().from_string("")


def _render_context(namespace: dict[str, Any]) -> RenderContext:
    """Bind the namespace to a fresh render context (locals stay per call)."""
    return RenderContext(_empty_template(), globals=dict(namespace))


def _expression(src: str) -> Any:
    """Parse *src* with Liquid's own grammars, output first, condition second.

    Liquid is two grammars: ``{{ ... }}`` takes a filtered value, ``{% if ... %}``
    takes a boolean expression. The workflow language is one grammar where a
    comparison is a value, so an expression the output grammar rejects is parsed
    as a condition. Both failures propagate as ``LiquidSyntaxError``.
    """
    try:
        template = _environment().parse("{{ " + src + " }}")
        return template.nodes[0].expression
    except LiquidSyntaxError:
        template = _environment().parse("{% if " + src + " %}x{% endif %}")
        return template.nodes[0].condition


def _normalize(value: Any) -> Any:
    """Map a Liquid ``Undefined`` (missing path) to ``None``."""
    if liquid.is_undefined(value):
        return None
    return value


def _eval_expression(expr: str, namespace: dict[str, Any]) -> Any:
    """Parse and run one block body. Raises ``LiquidError`` on wiring faults and
    ``ValueError`` from the shared filters' own strictness."""
    if not expr.strip():
        # ``{{ }}`` / ``{{  }}``: an empty body resolves to None, not a parse
        # error (Liquid raises "expected a primitive expression" otherwise).
        return None
    parsed = _expression(expr.strip())
    return _normalize(parsed.evaluate(_render_context(namespace)))


def _interpolate(template: str, namespace: dict[str, Any]) -> str:
    """Substitute every top-level ``{{ ... }}`` block, quote-aware.

    Reuses ``template._find_block_close`` (the shared scanner) so a literal
    ``}}`` inside a string argument does not close a block early, and keeps the
    raw-close fallback for a template the quote-aware scan cannot close --
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

    Single ``{{ ... }}`` block: typed value through Liquid's grammars. Anything
    else: interpolated string. The single vs. mixed decision and the block
    scanner are the shared ``template`` primitives, so the diagnostics and the
    evaluator cannot disagree about where a block ends.
    """
    stripped = template.strip()
    try:
        if _is_single_expression(stripped):
            return _eval_expression(stripped[2:-2].strip(), namespace)
        return _interpolate(template, namespace)
    except LiquidError as exc:
        # Normalize to the ValueError the current engine raises, so callers and
        # the harness see the same error *type*.
        raise ValueError(str(exc)) from exc


# -- Probe / diagnostics -----------------------------------------------------

class _ProbeNamespace(dict):
    """Namespace for the probe: every root exists, every leaf is absent.

    Access to a missing key hands back another ``_ProbeNamespace`` so a probe
    can walk ``inputs.a.b`` without real inputs, mirroring
    ``engine_current._ProbeNamespace``.
    """

    def __missing__(self, key: str) -> "_ProbeNamespace":  # noqa: UP037
        return _ProbeNamespace()


def _render_location(parts: tuple[Any, ...]) -> str | None:
    """Render a Liquid path location as dotted/indexed text, or ``None``.

    ``("inputs", "name")`` -> ``"inputs.name"``; ``("inputs", "tags", 0)`` ->
    ``"inputs.tags[0]"``. A nested path (``tags[foo]``) is not static and
    returns ``None`` so the walker descends into it instead.
    """
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if isinstance(part, int):
            rendered.append(f"[{part}]")
        elif isinstance(part, str):
            rendered.append(part if index == 0 else f".{part}")
        else:
            return None
    return "".join(rendered)


def _collect_leaves(text: str) -> list[str]:
    """Every substring *text* hands to the evaluator as a name to resolve.

    Walks the Liquid expression tree: each ``Path`` is a leaf; a path with a
    dynamic index (``tags[foo]``) contributes its static prefix and the index
    path separately. A text that does not parse is returned whole, so the gate
    reports the shape rather than staying silent.
    """
    src = text.strip()
    if not src:
        return []
    try:
        parsed = _expression(src)
    except LiquidSyntaxError:
        return [src]

    leaves: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, Path):
            parts = node.location()
            dynamic = [part for part in parts if isinstance(part, tuple)]
            if not dynamic:
                rendered = _render_location(parts)
                if rendered is not None:
                    leaves.append(rendered)
                return
            first = next(i for i, part in enumerate(parts) if isinstance(part, tuple))
            prefix = _render_location(parts[:first])
            if prefix:
                leaves.append(prefix)
            for part in dynamic:
                rendered = _render_location(part)
                if rendered:
                    leaves.append(rendered)
            return
        try:
            children = node.children()
        except Exception:  # noqa: BLE001 - tree shape is the library's
            children = []
        for child in children:
            walk(child)

    walk(parsed)
    return leaves


def _evaluator_rejects(text: str) -> str | None:
    """The evaluator's own complaint about how *text* is wired, or ``None``.

    Reports only what is about the author's text: a syntax error (unknown
    segment, reserved word, missing operand) and a registered filter used in an
    unsupported form (a ``ValueError`` from the strict wrapper, which quotes the
    segment back). Value errors about the probe's own placeholders are not
    rejections.
    """
    try:
        _eval_expression(text, {root: _ProbeNamespace() for root in _NAMESPACE_ROOTS})
    except LiquidSyntaxError as exc:
        return str(exc).split(":", 1)[0]
    except LiquidError as exc:
        message = str(exc)
        if "unknown filter" in message:
            return message.split(":", 1)[0]
        return None
    except ValueError as exc:
        message = str(exc)
        if "got '| " in message:
            return message.split(":", 1)[0]
        return None
    except Exception:  # noqa: BLE001 - probe values, not the author's text
        return None
    return None


def _has_incomplete_operand(text: str) -> bool:
    """True when *text* does not parse as a complete Liquid expression.

    An operator missing an operand (``inputs.a ==``, ``a and``, ``in inputs.a``)
    is a parse error in both Liquid grammars, which is the Liquid-native
    equivalent of the current engine's operator scan.
    """
    stripped = text.strip()
    if not stripped:
        return True
    try:
        _expression(stripped)
    except LiquidSyntaxError:
        return True
    return False
