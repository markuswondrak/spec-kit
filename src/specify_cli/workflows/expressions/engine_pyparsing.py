"""Variant C prototype: a pyparsing grammar replaces the hand-written parser.

Same scope as variant A (``engine_lark``): only the fragile part -- the string
splitting inside one ``{{ ... }}`` block -- is replaced. The template scanner
(``template``), the namespace (``namespace``), the filters (``filters``) and the
comparison semantics (``operators``) are reused unchanged. The module is a
deliberate 1:1 port of the Lark prototype so that the two candidates differ
only in the grammar technology, not in what is left to the library.

The module fulfils the full ``engine.py`` contract. Template-level names
(``_is_single_expression``, ``_first_unclosable_block``) are the shared
scanners from ``template``: the template level is deliberately untouched.
Everything the block body needs (grammar walk, filter dispatcher, probe and
syntax primitives) is rebuilt here on the pyparsing tree; that rebuilt surface
is the compatibility layer S5 measures.

The parser is built once per process, lazily, on first use; packrat caching is
enabled so sub-expressions are not re-parsed.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from pyparsing import (
    DelimitedList,
    Forward,
    Literal,
    MatchFirst,
    Opt,
    ParserElement,
    ParseBaseException,
    Regex,
    Suppress,
    ZeroOrMore,
)

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


# -- Grammar -----------------------------------------------------------------
# Tuple tags, same shapes as variant A; the probe and the real run share them.
_PATH = "PATH"
_LIST = "LIST"
_PIPE = "PIPE"
_FILTER = "FILTER"

# The grammar of the current language, restricted to one ``{{ }}`` block body.
# Same operators, tokens and precedences as the Lark grammar of variant A; the
# template level that surrounds a block is *not* modelled here.

_NOT_IN_T = r"(?<![\w-])not\s+in(?![\w-])"
_IN_T = r"(?<![\w-])in(?![\w-])"
_NOT_T = r"(?<![\w-])not(?![\w-])"
_AND_T = r"(?<![\w-])and(?![\w-])"
_OR_T = r"(?<![\w-])or(?![\w-])"
_FILTER_NAME_RE = r"[A-Za-z_][\w-]*"
_PATH_RE = r"[A-Za-z_][\w-]*(\[-?\d+\])?(\.[\w-]+(\[-?\d+\])?)*"
_STRING_RE = (r"'[^']*'", r'"[^"]*"')
_NUMBER_RE = r"-?\d+(\.\d+)?"


def _fold_pipe(tokens: Any) -> list[Any]:
    node = tokens[0]
    for filt in tokens[1:]:
        node = (_PIPE, node, filt)
    return [node]


def _fold_compare(tokens: Any) -> list[Any]:
    node = tokens[0]
    index = 1
    while index < len(tokens):
        node = (str(tokens[index]), node, tokens[index + 1])
        index += 2
    return [node]


def _fold(tag: str) -> Any:
    def fold(tokens: Any) -> list[Any]:
        node = tokens[0]
        index = 2
        while index < len(tokens):
            node = (tag, node, tokens[index])
            index += 2
        return [node]

    return fold


@lru_cache(maxsize=1)
def _parser() -> ParserElement:
    """Build the parser once, on first use, so CLI start-up pays nothing."""
    ParserElement.enable_packrat()
    expr = Forward()

    quoted = MatchFirst([Regex(_STRING_RE[0]), Regex(_STRING_RE[1])])
    quoted.set_parse_action(lambda tokens: str(tokens[0])[1:-1])
    number = Regex(_NUMBER_RE).set_parse_action(
        lambda tokens: [
            float(str(tokens[0])) if "." in str(tokens[0]) else int(str(tokens[0]))
        ]
    )
    path = Regex(_PATH_RE).set_parse_action(
        lambda tokens: [(_PATH, str(tokens[0]))]
    )

    list_lit = (
        Suppress("[")
        + Opt(expr)
        + ZeroOrMore(Suppress(",") + Opt(expr))
        + Suppress("]")
    ).set_parse_action(lambda tokens: [(_LIST, *tokens)])

    paren = Suppress("(") + expr + Suppress(")")

    atom = MatchFirst([list_lit, paren, quoted, number, path])

    filter_name = Regex(_FILTER_NAME_RE)
    filter_bare = filter_name.copy().set_parse_action(
        lambda tokens: [(_FILTER, str(tokens[0]), [], False)]
    )
    filter_call = (
        filter_name.copy()
        + Suppress("(")
        + Opt(DelimitedList(expr))
        + Suppress(")")
    ).set_parse_action(lambda tokens: [(_FILTER, str(tokens[0]), list(tokens[1:]), True)])
    filter_expr = MatchFirst([filter_call, filter_bare])

    pipe = (atom + ZeroOrMore(Suppress("|") + filter_expr)).set_parse_action(_fold_pipe)

    cmp_op = MatchFirst(
        [
            Literal("!="),
            Literal("=="),
            Literal(">="),
            Literal("<="),
            Literal(">"),
            Literal("<"),
            Regex(_NOT_IN_T),
            Regex(_IN_T),
        ]
    )
    comparison = (pipe + ZeroOrMore(cmp_op + pipe)).set_parse_action(_fold_compare)

    not_expr = Forward()
    not_expr <<= MatchFirst(
        [
            (Regex(_NOT_T) + not_expr).set_parse_action(
                lambda tokens: [("not", tokens[1])]
            ),
            comparison,
        ]
    )

    and_expr = (not_expr + ZeroOrMore(Regex(_AND_T) + not_expr)).set_parse_action(
        _fold("and")
    )
    or_expr = (and_expr + ZeroOrMore(Regex(_OR_T) + and_expr)).set_parse_action(
        _fold("or")
    )

    expr <<= or_expr
    # pyparsing expands tabs in the input by default; the current evaluator
    # keeps them, e.g. inside quoted operands. This is the documented switch.
    expr.parse_with_tabs()
    return expr


def _parse(expr: str) -> Any:
    """Parse one block body into the tuple AST. Raises ``pyparsing`` errors."""
    return _parser().parse_string(expr, parse_all=True)[0]


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
    except ParseBaseException as exc:
        raise ValueError(f"invalid expression {expr.strip()!r}: {exc}") from exc
    return _eval(ast, namespace)


def _interpolate_expressions(template: str, namespace: dict[str, Any]) -> str:
    """Substitute every top-level ``{{ ... }}`` block, quote-aware.

    The template level is the current scanner, unchanged: only the body handed
    to ``_evaluate_simple_expression`` goes through pyparsing.
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
