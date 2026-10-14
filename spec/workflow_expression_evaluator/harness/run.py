"""Run the behavior corpus against every available backend and print statistics.

Usage (from the repository root):

    .venv/bin/python spec/workflow_expression_evaluator/harness/run.py

Writes a machine-readable report to reports/report.json and a Markdown summary
to reports/report.md.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harness.backends import load_backends  # noqa: E402
from harness.corpus import ALL_CASES, COMPAT, REQUIRED, Case  # noqa: E402
from harness.bench import BenchResult, benchmark, write_bench_section  # noqa: E402
from harness.metrics import (  # noqa: E402
    FileMetrics,
    OwnedMetrics,
    measure,
    measure_owned,
    write_section,
)

from specify_cli.workflows.base import StepContext  # noqa: E402

REPORTS_DIR = _ROOT / "reports"


def build_context(data: dict[str, Any]) -> StepContext:
    return StepContext(
        inputs=data.get("inputs", {}),
        steps=data.get("steps", {}),
        item=data.get("item"),
        fan_in=data.get("fan_in", {}),
        run_id=data.get("run_id", ""),
    )


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


@dataclass
class Result:
    case_id: str
    backend: str
    requirement: str
    kind: str
    status: str  # "match" | "diff" | "raised"
    detail: str = ""


def run_case(backend: Any, case: Case) -> Result:
    ctx = build_context(case.context)
    call = backend.evaluate_expression if case.kind == "expression" else backend.evaluate_condition
    try:
        got = call(case.template, ctx)
        raised = None
    except Exception as exc:  # noqa: BLE001 - the harness records any exception
        got, raised = None, exc

    if case.expect == "error":
        # The exception TYPE is part of the contract (S3): callers catch
        # ValueError. Any other type is a divergence, not a match.
        if isinstance(raised, ValueError):
            return Result(case.id, backend.name, case.requirement, case.kind, "match")
        if raised is not None:
            return Result(case.id, backend.name, case.requirement, case.kind, "diff",
                          f"expected ValueError, got {type(raised).__name__}: {raised}")
        return Result(case.id, backend.name, case.requirement, case.kind, "diff",
                      f"expected error, got {got!r}")

    if raised is not None:
        return Result(case.id, backend.name, case.requirement, case.kind, "raised",
                      f"{type(raised).__name__}: {raised}; expected {case.value!r}")
    if not _same(got, case.value):
        return Result(case.id, backend.name, case.requirement, case.kind, "diff",
                      f"got {got!r}, expected {case.value!r}")
    return Result(case.id, backend.name, case.requirement, case.kind, "match")


def _percent(matched: int, total: int) -> str:
    return f"{(100.0 * matched / total):5.1f}%" if total else "  n/a"


def print_backend_report(backend: Any, results: list[Result]) -> None:
    print(f"\n=== backend: {backend.name}  (loc: {backend.loc()}) ===")
    buckets: dict[tuple[str, str], list[Result]] = defaultdict(list)
    for r in results:
        buckets[(r.requirement, r.kind)].append(r)

    for requirement in (REQUIRED, COMPAT):
        for kind in ("expression", "condition"):
            bucket = buckets.get((requirement, kind))
            if not bucket:
                continue
            matched = sum(1 for r in bucket if r.status == "match")
            print(f"  {requirement:8} {kind:10} {matched:3}/{len(bucket):<3} {_percent(matched, len(bucket))}")

    required = [r for r in results if r.requirement == REQUIRED]
    req_ok = sum(1 for r in required if r.status == "match")
    total_ok = sum(1 for r in results if r.status == "match")
    print(f"  {'REQUIRED TOTAL':19} {req_ok:3}/{len(required):<3} {_percent(req_ok, len(required))}"
          f"   <-- hard requirement")
    print(f"  {'overall':19} {total_ok:3}/{len(results):<3} {_percent(total_ok, len(results))}")

    diffs = [r for r in results if r.status != "match"]
    if diffs:
        print(f"  divergences ({len(diffs)}):")
        for r in diffs:
            print(f"    [{r.status}] {r.case_id}: {r.detail}")


def write_markdown(
    all_results: dict[str, list[Result]],
    locs: dict[str, int],
    metrics: list[FileMetrics],
    owned: list[OwnedMetrics],
    bench: list[BenchResult],
) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "report.md"
    lines = ["# Expression evaluator: backend comparison", ""]
    lines.append(f"Corpus: {len(ALL_CASES)} cases "
                 f"({sum(1 for c in ALL_CASES if c.requirement == REQUIRED)} required, "
                 f"{sum(1 for c in ALL_CASES if c.requirement == COMPAT)} compat)")
    lines.append("")
    lines.append("LOC = non-blank, non-comment lines of the engine module (docstrings "
                 "included; legacy column). Use *Engine-owned code* below for size comparisons.")
    lines.append("")
    lines.append("| Backend | LOC | required | compat | overall |")
    lines.append("|---|---|---|---|---|")
    for name, results in all_results.items():
        required = [r for r in results if r.requirement == REQUIRED]
        compat = [r for r in results if r.requirement == COMPAT]
        req_ok = sum(1 for r in required if r.status == "match")
        com_ok = sum(1 for r in compat if r.status == "match")
        tot_ok = sum(1 for r in results if r.status == "match")
        lines.append(
            f"| {name} | {locs.get(name, '?')} | {req_ok}/{len(required)} | "
            f"{com_ok}/{len(compat)} | {tot_ok}/{len(results)} |"
        )
    lines.append("")
    lines.extend(write_section(metrics, owned))
    lines.extend(write_bench_section(bench))
    for name, results in all_results.items():
        diffs = [r for r in results if r.status != "match"]
        if diffs:
            lines.append(f"## {name}: divergences")
            lines.append("")
            for r in diffs:
                lines.append(f"- `[{r.status}]` **{r.case_id}** ({r.requirement}): {r.detail}")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    backends = load_backends()
    if backends and backends[0].name != "current":
        print("warning: 'current' is not the first backend", file=sys.stderr)

    all_results: dict[str, list[Result]] = {}
    locs: dict[str, int] = {}
    for backend in backends:
        try:
            locs[backend.name] = backend.loc()
        except Exception as exc:  # noqa: BLE001
            locs[backend.name] = -1
            print(f"warning: loc() failed for {backend.name}: {exc}", file=sys.stderr)
        results = [run_case(backend, case) for case in ALL_CASES]
        all_results[backend.name] = results
        print_backend_report(backend, results)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        metrics = measure()
        owned = measure_owned()
    except Exception as exc:  # noqa: BLE001 - metrics are optional evidence
        metrics, owned = [], []
        print(f"warning: complexity metrics failed: {exc}", file=sys.stderr)

    lark_backend = next((b for b in backends if b.name == "lark"), None)

    def corpus_for_lark() -> tuple[int, int, list[str]]:
        if lark_backend is None:
            return 0, 0, []
        results = [run_case(lark_backend, case) for case in ALL_CASES]
        diverging = [r.case_id for r in results if r.status != "match"]
        return len(results) - len(diverging), len(results), diverging

    try:
        bench = benchmark(corpus_for_lark)
    except Exception as exc:  # noqa: BLE001 - runtime is optional evidence
        bench = []
        print(f"warning: benchmark failed: {exc}", file=sys.stderr)

    (REPORTS_DIR / "report.json").write_text(
        json.dumps(
            {
                "corpus_size": len(ALL_CASES),
                "backends": {
                    name: [asdict(r) for r in results]
                    for name, results in all_results.items()
                },
                "metrics": [asdict(entry) for entry in metrics],
                "owned": [asdict(entry) for entry in owned],
                "runtime": [asdict(entry) for entry in bench],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    md = write_markdown(all_results, locs, metrics, owned, bench)
    print(f"\nJSON: {REPORTS_DIR / 'report.json'}\nMarkdown: {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
