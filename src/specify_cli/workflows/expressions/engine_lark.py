"""Variant A prototype: a Lark grammar replaces the hand-written parser.

Only the fragile part -- the string splitting inside one ``{{ ... }}`` block --
is replaced. The template scanner (``template``), the namespace
(``namespace``), the filters (``filters``) and the comparison semantics
(``operators``) are reused unchanged. This module answers the question "does a
library remove complexity" for the parser layer alone.

The module fulfils the full ``engine.py`` contract. Template-level names
(``_is_single_expression``, ``_first_unclosable_block``) are the shared
scanners from ``template``: the template level is deliberately untouched.
Everything the block body needs (grammar walk, filter dispatcher, probe and
syntax primitives) is rebuilt here on the Lark tree; that rebuilt surface is
the compatibility layer S5 measures.

No generated parser is checked in (``lark.tools.standalone`` is deliberately
not used): the parser is built once per process, lazily, on first use.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from lark import Lark, Token, Transformer
from lark.exceptions import UnexpectedInput

from .filters import (
    _REGISTERED_FILTERS,
    _ZERO_ARG_FILTERS,
    _filter_contains,
    _filter_default,
    _filter_join,
    _filter_map,
    _filter_split,
)
from .namespace import _NAMESPACE_ROOTS, _resolve_dot_path
from .operators import _safe_compare, _safe_membership
from .template import (
    _collapse_whitespace,
    _find_block_close,
    _first_unclosable_block,
    _is_single_expression,
)


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


# The grammar of the current language, restricted to one ``{{ }}`` block body.
# ``and``/``or``/``not``/``in``/``not in`` and the comparisons are the same
# operators; the template level that surrounds a block is *not* modelled here.
_GRAMMAR = r"""
?start: expr

?expr: or_expr

?or_expr: and_expr
        | or_expr OR and_expr       -> or_op

?and_expr: not_expr
        | and_expr AND not_expr     -> and_op

?not_expr: NOT not_expr             -> not_op
        |  comparison

?comparison: pipe
        | comparison EQ pipe        -> cmp_eq
        | comparison NE pipe        -> cmp_ne
        | comparison GE pipe        -> cmp_ge
        | comparison LE pipe        -> cmp_le
        | comparison GT pipe        -> cmp_gt
        | comparison LT pipe        -> cmp_lt
        | comparison IN_T pipe      -> cmp_in
        | comparison NOT_IN_T pipe  -> cmp_not_in

?pipe: atom
        | pipe PIPE filter          -> pipe_filter

filter: FILTER_NAME                             -> filter_bare
        | FILTER_NAME "(" [expr ("," expr)*] ")" -> filter_call

?atom: STRING      -> string
        | NUMBER    -> number
        | list_lit
        | path
        | "(" expr ")"

list_lit: "[" list_body "]"
list_body: [expr] ("," [expr])*
path: PATH

NOT_IN_T: /(?<![\w-])not\s+in(?![\w-])/
IN_T: /(?<![\w-])in(?![\w-])/
NOT: /(?<![\w-])not(?![\w-])/
AND: /(?<![\w-])and(?![\w-])/
OR: /(?<![\w-])or(?![\w-])/
EQ: "=="
NE: "!="
GE: ">="
LE: "<="
GT: ">"
LT: "<"
PIPE: "|"
FILTER_NAME: /[A-Za-z_][\w-]*/
PATH: /[A-Za-z_][\w-]*(\[-?\d+\])?(\.[\w-]+(\[-?\d+\])?)*/
STRING: /'[^']*'/ | /"[^"]*"/
NUMBER: /-?\d+(\.\d+)?/
%import common.WS
%ignore WS
"""


@lru_cache(maxsize=1)
def _parser() -> Lark:
    """Build the parser once, on first use, so CLI start-up pays nothing."""
    # ``maybe_placeholders=False`` keeps absent optional groups out of the
    # children, so ``[]`` is an empty list rather than ``[None]``.
    return Lark(_GRAMMAR, parser="earley", maybe_placeholders=False)


def _without_tokens(children: list[Any]) -> list[Any]:
    """Drop the operator tokens Lark keeps as children of a rule."""
    return [child for child in children if not isinstance(child, Token)]


# -- Parse tree --------------------------------------------------------------
# Lark transforms bottom-up into bare tuples; evaluation happens in a second
# pass so the probe (``_collect_leaves``) and the real run share one AST shape.

_PATH = "PATH"
_LIST = "LIST"
_PIPE = "PIPE"
_FILTER = "FILTER"


class _ToAst(Transformer):
    def string(self, children: list[Any]) -> str:
        return str(children[0])[1:-1]

    def number(self, children: list[Any]) -> Any:
        text = str(children[0])
        return float(text) if "." in text else int(text)

    def path(self, children: list[Any]) -> tuple[str, str]:
        return (_PATH, str(children[0]))

    def list_body(self, children: list[Any]) -> list[Any]:
        return [child for child in children if child is not None]

    def list_lit(self, children: list[Any]) -> tuple[Any, ...]:
        return (_LIST, *children[0])

    def filter_bare(self, children: list[Any]) -> tuple[Any, ...]:
        return (_FILTER, str(children[0]), [], False)

    def filter_call(self, children: list[Any]) -> tuple[Any, ...]:
        return (_FILTER, str(children[0]), list(children[1:]), True)

    def pipe_filter(self, children: list[Any]) -> tuple[Any, ...]:
        value, filt = _without_tokens(children)
        return (_PIPE, value, filt)

    def cmp_eq(self, children: list[Any]) -> tuple[Any, ...]:
        return ("==", *_without_tokens(children))

    def cmp_ne(self, children: list[Any]) -> tuple[Any, ...]:
        return ("!=", *_without_tokens(children))

    def cmp_ge(self, children: list[Any]) -> tuple[Any, ...]:
        return (">=", *_without_tokens(children))

    def cmp_le(self, children: list[Any]) -> tuple[Any, ...]:
        return ("<=", *_without_tokens(children))

    def cmp_gt(self, children: list[Any]) -> tuple[Any, ...]:
        return (">", *_without_tokens(children))

    def cmp_lt(self, children: list[Any]) -> tuple[Any, ...]:
        return ("<", *_without_tokens(children))

    def cmp_in(self, children: list[Any]) -> tuple[Any, ...]:
        return ("in", *_without_tokens(children))

    def cmp_not_in(self, children: list[Any]) -> tuple[Any, ...]:
        return ("not in", *_without_tokens(children))

    def or_op(self, children: list[Any]) -> tuple[Any, ...]:
        return ("or", *_without_tokens(children))

    def and_op(self, children: list[Any]) -> tuple[Any, ...]:
        return ("and", *_without_tokens(children))

    def not_op(self, children: list[Any]) -> tuple[Any, ...]:
        return ("not", *_without_tokens(children))


def _parse(expr: str) -> Any:
    """Parse one block body into the tuple AST. Raises ``lark`` errors."""
    return _ToAst().transform(_parser().parse(expr))


# -- Evaluation --------------------------------------------------------------

# Set only while ``_collect_leaves`` probes an expression; None otherwise. Same
# role as the current engine's sink, so the drift guard tests keep working.
_leaf_sink: ContextVar[list[str] | None] = ContextVar("_leaf_sink", default=None)


class _ProbeNamespace(dict):
    """Namespace for the parse probe: every root exists, every leaf is absent."""

    def __missing__(self, key: str) -> "_ProbeNamespace":  # noqa: UP037
        return _ProbeNamespace()


# Order matters -- multi-char operators first, so "!=" is not split as "!" + "=".
_COMPARISON_OPERATORS = ("!=", "==", ">=", "<=", ">", "<", " not in ", " in ")

# Derived so the checks cannot drift from the operator table, mirroring the
# current engine; the diagnostics' end-of-core scan reads this.
_WORD_OPERATORS = tuple(
    op for op in (" or ", " and ") + _COMPARISON_OPERATORS if op.startswith(" ")
)


def _apply_filter(value: Any, filt: tuple[Any, ...], namespace: dict[str, Any]) -> Any:
    """Apply one ``("FILTER", name, [arg_nodes], has_parens)`` to *value*.

    Arguments are evaluated here, before the filter runs, so their leaves are
    recorded even when the value makes the filter raise. The arity check lives
    here -- not in the grammar -- so a multi-argument call is reported as the
    unsupported form it is, mirroring the current dispatcher's strictness and
    messages. ``has_parens`` distinguishes ``| from_json`` (valid) from
    ``| from_json()`` (a mis-wired zero-argument filter, refused).
    """
    _, name, arg_nodes, has_parens = filt
    args = [_eval(node, namespace) for node in arg_nodes]
    has_arg = bool(args)

    if name in _ZERO_ARG_FILTERS:
        if has_parens:
            raise ValueError(
                f"{name}: expected '| {name}' with no arguments or trailing "
                f"tokens, got '| {name}(...)'"
            )
        return _ZERO_ARG_FILTERS[name](value)

    if len(args) == 1:
        arg = args[0]
        if name == "default":
            return _filter_default(value, arg)
        if name == "join":
            return _filter_join(value, arg)
        if name == "map":
            return _filter_map(value, arg)
        if name == "contains":
            return _filter_contains(value, arg)
        if name == "split":
            return _filter_split(value, arg)
    elif not args and not has_parens and name == "default":
        return _filter_default(value)

    display = f"{name}(...)" if has_parens or has_arg else name
    expected = (
        "expected one of default or default('x'), join('sep'), "
        "map('attr'), contains('s'), split('sep'), from_json, upper, "
        "lower, length, or to_json"
    )
    if name in _REGISTERED_FILTERS:
        raise ValueError(
            f"filter '{name}' used in an unsupported form (got '| {display}'): "
            f"{expected}"
        )
    raise ValueError(f"unknown filter '{name}': {expected} (got '| {display}')")


def _eval(node: Any, namespace: dict[str, Any]) -> Any:
    """Evaluate a tuple AST node against *namespace*."""
    if not isinstance(node, tuple):
        return node

    tag = node[0]
    if tag == _PATH:
        text = node[1]
        if "." not in text and "[" not in text:
            lowered = text.lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
            if lowered in ("none", "null"):
                return None
        sink = _leaf_sink.get()
        if sink is not None:
            sink.append(text)
        return _resolve_dot_path(namespace, text)

    if tag == _LIST:
        return [_eval(item, namespace) for item in node[1:]]

    if tag == _PIPE:
        value = _eval(node[1], namespace)
        try:
            return _apply_filter(value, node[2], namespace)
        except Exception:  # noqa: BLE001 - only while probing
            if _leaf_sink.get() is None:
                raise
            # A probe placeholder made a filter raise; carry the next placeholder
            # so the rest of the chain is still walked, as the current engine
            # does. The argument's leaves were recorded before the raise.
            return _ProbeNamespace()

    if tag == "not":
        return not bool(_eval(node[1], namespace))

    left = _eval(node[1], namespace)
    right = _eval(node[2], namespace)
    if tag == "or":
        return bool(left) or bool(right)
    if tag == "and":
        return bool(left) and bool(right)
    if tag == "==":
        return left == right
    if tag == "!=":
        return left != right
    if tag in (">", "<", ">=", "<="):
        return _safe_compare(left, right, tag)
    if tag == "in":
        return _safe_membership(left, right, negate=False)
    if tag == "not in":
        return _safe_membership(left, right, negate=True)
    raise ValueError(f"unsupported expression node {tag!r}")


def _evaluate_simple_expression(expr: str, namespace: dict[str, Any]) -> Any:
    """Evaluate the body of one ``{{ }}`` block against *namespace*.

    A syntax error is raised as ``ValueError``, the type the evaluator already
    uses for a malformed expression, rather than leaking the parser's own
    exception. This is a compatibility shim, not a semantics change; the current
    engine mostly resolves unrecognised shapes to ``None`` instead of raising,
    which is the deviation S5 evaluates.
    """
    collapsed = _collapse_whitespace(expr)
    if not collapsed:
        return None
    try:
        ast = _parse(collapsed)
    except UnexpectedInput as exc:
        raise ValueError(f"invalid expression {expr.strip()!r}: {exc}") from exc
    return _eval(ast, namespace)


def _interpolate_expressions(template: str, namespace: dict[str, Any]) -> str:
    """Substitute every top-level ``{{ ... }}`` block, quote-aware.

    The template level is the current scanner, unchanged: only the body handed
    to ``_evaluate_simple_expression`` goes through Lark.
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
        val = _evaluate_simple_expression(template[start + 2:close].strip(), namespace)
        out.append(str(val) if val is not None else "")
        i = close + 2
    return "".join(out)


def _evaluate_template(template: str, namespace: dict[str, Any]) -> Any:
    """Evaluate *template*: a single block stays typed, text is interpolated."""
    stripped = template.strip()
    if _is_single_expression(stripped):
        return _evaluate_simple_expression(stripped[2:-2].strip(), namespace)
    return _interpolate_expressions(template, namespace)


def _evaluator_rejects(text: str) -> str | None:
    """The evaluator's own complaint about how *text* is wired, or ``None``.

    Only the two filter-wiring failures the dispatcher raises about the
    expression itself are reported; a parse error or a probe-value type error
    is about the placeholder or the syntax, not the wiring, and returns None.
    """
    try:
        _evaluate_simple_expression(
            text, {root: _ProbeNamespace() for root in _NAMESPACE_ROOTS}
        )
    except ValueError as exc:
        message = str(exc)
        if "got '| " in message:
            return message.split(":", 1)[0]
    except Exception:  # noqa: BLE001 - parse/probe values, not the author's text
        return None
    return None


def _collect_leaves(text: str) -> list[str]:
    """Every substring *text* hands to the evaluator as a name to resolve.

    Walks the same AST the real run walks, with the leaf sink armed, so the
    diagnostics read the names the engine will actually look up.
    """
    leaves: list[str] = []
    token = _leaf_sink.set(leaves)
    try:
        collapsed = _collapse_whitespace(text)
        if collapsed:
            _eval(_parse(collapsed), {root: _ProbeNamespace() for root in _NAMESPACE_ROOTS})
    except Exception:  # noqa: BLE001 - probe values / syntax, reported elsewhere
        pass
    finally:
        _leaf_sink.reset(token)
    return leaves


def _has_incomplete_operand(text: str) -> bool:
    """True when *text* does not parse as a complete expression.

    The grammar is the operator table: text with an operator missing an operand
    (``inputs.a ==``, ``and inputs.a``, ``inputs.a not in``) cannot parse, so the
    check cannot drift from what the engine accepts.
    """
    stripped = text.strip()
    if not stripped:
        return True
    try:
        _parse(_collapse_whitespace(stripped))
    except Exception:  # noqa: BLE001 - any parse failure is an incomplete operand
        return True
    return False
