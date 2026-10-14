"""Indicative runtime per engine, plus the Lark LALR probe.

Times ``_evaluate_template`` on a fixed set of expressions every A/C engine
supports. An engine that raises on one of them is reported as ``n/a`` rather
than timed on a smaller set, so the numbers stay comparable. Absolute values
depend on the machine; the ratio to ``current`` is the point.

The LALR probe answers one question from the S5 review: was Lark's Earley
parser a necessary choice? It rebuilds ``engine_lark``'s own grammar with
``parser="lalr"`` and keyword terminal priorities (two documented Lark
options, no grammar rewrite), swaps it in for the duration of the probe, and
reports corpus score and runtime. It is evidence for A2, not a backend.
"""

from __future__ import annotations

import importlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_ENGINES = ("engine_current", "engine_lark", "engine_lark_a2", "engine_pyparsing", "engine_jinja2", "engine_liquid")

_EXPRESSIONS = (
    "{{ inputs.count > 5 and inputs.name == 'x' }}",
    "{{ steps.run-tests.output.exit_code == 0 }}",
    "{{ inputs.tags | join(', ') }}",
    "{{ (inputs.count > 3 or inputs.limit < 2) and not inputs.missing }}",
    "{{ 'a' in inputs.tags }}",
    "Count is {{ inputs.count }} and mode {{ inputs.mode | upper }}",
)

_NAMESPACE: dict[str, Any] = {
    "inputs": {"count": 7, "limit": 5, "tags": ["a", "b"], "name": "x", "mode": "read"},
    "steps": {"run-tests": {"output": {"exit_code": 0}}},
    "item": None,
    "fan_in": {},
    "context": {},
}

_ROUNDS = 300
_REPETITIONS = 5


@dataclass
class BenchResult:
    engine: str
    per_expression_us: float | None
    first_call_ms: float | None
    note: str = ""
    corpus: str = ""


def _time(evaluate: Callable[[str, dict[str, Any]], Any]) -> tuple[float, float]:
    start = time.perf_counter()
    evaluate(_EXPRESSIONS[0], _NAMESPACE)
    first = (time.perf_counter() - start) * 1000
    passes: list[float] = []
    for _ in range(_REPETITIONS):
        start = time.perf_counter()
        for _ in range(_ROUNDS):
            for expression in _EXPRESSIONS:
                evaluate(expression, _NAMESPACE)
        passes.append(
            (time.perf_counter() - start) / (_ROUNDS * len(_EXPRESSIONS)) * 1e6
        )
    return sorted(passes)[len(passes) // 2], first


def _bench_engine(name: str) -> BenchResult:
    try:
        module = importlib.import_module(f"specify_cli.workflows.expressions.{name}")
    except ImportError as exc:
        return BenchResult(name, None, None, f"not importable: {exc}")
    for expression in _EXPRESSIONS:
        try:
            module._evaluate_template(expression, _NAMESPACE)
        except Exception as exc:  # noqa: BLE001 - unsupported shape, reported
            return BenchResult(name, None, None, f"n/a: {expression!r} raises {type(exc).__name__}")
    per, first = _time(module._evaluate_template)
    return BenchResult(name, per, first)


def _lalr_probe(corpus: Callable[[], tuple[int, int, list[str]]]) -> BenchResult:
    try:
        from lark import Lark

        from specify_cli.workflows.expressions import engine_lark
    except ImportError as exc:
        return BenchResult("engine_lark (LALR probe)", None, None, f"not importable: {exc}")
    grammar = re.sub(r"^(NOT_IN_T|IN_T|NOT|AND|OR):", r"\1.2:", engine_lark._GRAMMAR, flags=re.M)
    parser = Lark(grammar, parser="lalr", maybe_placeholders=False)
    original = engine_lark._parser
    engine_lark._parser = lambda: parser
    try:
        per, first = _time(engine_lark._evaluate_template)
        matched, total, diverging = corpus()
        rejects_not_in = engine_lark._has_incomplete_operand("not in inputs.a")
    finally:
        engine_lark._parser = original
    note = "parser='lalr' + keyword priority .2; `not in inputs.a` " + (
        "rejected (as current)" if rejects_not_in else "accepted (Earley-like)"
    )
    return BenchResult(
        "engine_lark (LALR probe)", per, first, note,
        f"{matched}/{total}" + (f" (diverging: {', '.join(diverging)})" if diverging else ""),
    )


def benchmark(corpus_for_lark: Callable[[], tuple[int, int, list[str]]]) -> list[BenchResult]:
    results = [_bench_engine(name) for name in _ENGINES]
    results.append(_lalr_probe(corpus_for_lark))
    return results


def write_bench_section(results: list[BenchResult]) -> list[str]:
    baseline = next(
        (r.per_expression_us for r in results if r.engine == "engine_current"), None
    )
    lines = [
        "## Runtime (indicative)",
        "",
        f"`_evaluate_template` over {len(_EXPRESSIONS)} expressions x {_ROUNDS} rounds, "
        f"median of {_REPETITIONS} passes (harness/bench.py). `n/a` = the engine rejects "
        "one of the expressions, so it is not timed on a smaller set. Machine-dependent "
        "and load-sensitive; compare the ratio, not the microseconds.",
        "",
        "| Engine | per expression | x current | first call | corpus | note |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        per = "-" if r.per_expression_us is None else f"{r.per_expression_us:.0f} us"
        ratio = (
            "-"
            if r.per_expression_us is None or not baseline
            else f"{r.per_expression_us / baseline:.1f}x"
        )
        first = "-" if r.first_call_ms is None else f"{r.first_call_ms:.1f} ms"
        note = r.note.replace("|", "\\|")
        corpus = (r.corpus or "-").replace("|", "\\|")
        lines.append(f"| `{r.engine}` | {per} | {ratio} | {first} | {corpus} | {note} |")
    lines.append("")
    return lines
