"""Declarative, backend-agnostic behavior corpus for the workflow expression evaluator.

Each case pairs a template (or condition) with a context and the outcome we
believe is correct. ``requirement`` separates the two tiers agreed in the plan:

* ``required`` -- a shape that occurs in a real workflow YAML. A candidate that
  diverges here would break an existing workflow.
* ``compat``   -- supported by the evaluator and covered by tests/docs, but not
  used by any real workflow. Divergence here is a compatibility cost, not a
  correctness bug for shipped workflows.

``expect`` is ``"value"`` (compare ``value``) or ``"error"`` (any exception is
acceptable; error *messages* are deliberately not compared across backends).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


REQUIRED = "required"
COMPAT = "compat"


@dataclass(frozen=True)
class Case:
    id: str
    kind: str  # "expression" | "condition"
    template: str
    context: dict[str, Any] = field(default_factory=dict)
    requirement: str = COMPAT
    expect: str = "value"
    value: Any = None
    note: str = ""


def _ctx(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"inputs": {}, "steps": {}, "item": None, "fan_in": {}, "run_id": ""}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tier "required": the shapes that actually occur in workflow YAMLs.
#
# Source: the three workflow files under workflows/ (speckit, bugfix, assess).
# Only variable access and interpolation-with-text appear there; nothing else.
# ---------------------------------------------------------------------------

REQUIRED_CASES: list[Case] = [
    Case("req.var.integration", "expression", "{{ inputs.integration }}",
         _ctx(inputs={"integration": "copilot"}), REQUIRED, "value", "copilot"),
    Case("req.var.spec", "expression", "{{ inputs.spec }}",
         _ctx(inputs={"spec": "001-login"}), REQUIRED, "value", "001-login"),
    Case("req.var.idea", "expression", "{{ inputs.idea }}",
         _ctx(inputs={"idea": "add login"}), REQUIRED, "value", "add login"),
    Case("req.var.report", "expression", "{{ inputs.report }}",
         _ctx(inputs={"report": "report.md"}), REQUIRED, "value", "report.md"),
    Case("req.var.slug", "expression", "{{ inputs.slug }}",
         _ctx(inputs={"slug": "add-login"}), REQUIRED, "value", "add-login"),
    Case("req.interp.idea-slug", "expression", "{{ inputs.idea }} slug={{ inputs.slug }}",
         _ctx(inputs={"idea": "add login", "slug": "add-login"}), REQUIRED, "value",
         "add login slug=add-login"),
    Case("req.interp.slug", "expression", "slug={{ inputs.slug }}",
         _ctx(inputs={"slug": "add-login"}), REQUIRED, "value", "slug=add-login"),
]


# ---------------------------------------------------------------------------
# Tier "compat": supported and tested, but not used by shipped workflows.
# ---------------------------------------------------------------------------

COMPAT_CASES: list[Case] = [
    # --- path resolution ---------------------------------------------------
    Case("path.steps", "expression", "{{ steps.specify.output.file }}",
         _ctx(steps={"specify": {"output": {"file": "spec.md"}}}), value="spec.md"),
    Case("path.hyphen", "expression", "{{ steps.run-tests.output.exit_code }}",
         _ctx(steps={"run-tests": {"output": {"exit_code": 1}}}), value=1),
    Case("path.context-run-id", "expression", "{{ context.run_id }}",
         _ctx(run_id="a1b2c3"), value="a1b2c3"),
    Case("path.context-run-id-default", "expression", "{{ context.run_id }}",
         _ctx(), value=""),
    Case("path.item", "expression", "{{ item.name }}",
         _ctx(item={"name": "n1"}), value="n1"),
    Case("path.fan-in", "expression", "{{ fan_in.results }}",
         _ctx(fan_in={"results": [1, 2]}), value=[1, 2]),
    Case("path.index", "expression", "{{ steps.t.output.list[0].file }}",
         _ctx(steps={"t": {"output": {"list": [{"file": "a.md"}, {"file": "b.md"}]}}}),
         value="a.md"),
    Case("path.index-negative", "expression", "{{ steps.t.output.list[-1].file }}",
         _ctx(steps={"t": {"output": {"list": [{"file": "a.md"}, {"file": "b.md"}]}}}),
         value="b.md"),
    Case("path.missing", "expression", "{{ inputs.missing }}", _ctx(), value=None),
    Case("path.missing-intermediate", "expression", "{{ inputs.a.b }}",
         _ctx(inputs={"a": {}}), value=None),

    # --- interpolation -----------------------------------------------------
    Case("interp.multi", "expression", "{{ context.run_id }} {{ inputs.issue }}",
         _ctx(run_id="47c5eb4b", inputs={"issue": "23"}), value="47c5eb4b 23"),
    Case("interp.adjacent", "expression", "{{ inputs.a }}{{ inputs.b }}",
         _ctx(inputs={"a": "foo", "b": "bar"}), value="foobar"),
    Case("interp.run-id", "expression", "RUN_ID={{ context.run_id }}",
         _ctx(run_id="deadbeef"), value="RUN_ID=deadbeef"),
    Case("interp.item", "expression", "item={{ item }}",
         _ctx(item="row-1"), value="item=row-1"),
    Case("interp.close-brace-arg", "expression",
         "{{ inputs.name }}: {{ inputs.missing | default('}}') }}",
         _ctx(inputs={"name": "Bob"}), value="Bob: }}"),
    Case("interp.open-brace-arg", "expression",
         "{{ inputs.name }} {{ inputs.missing | default('{{') }}",
         _ctx(inputs={"name": "Bob"}), value="Bob {{"),
    Case("interp.empty-block", "expression", "{{ }}", _ctx(), value=""),

    # --- comparisons -------------------------------------------------------
    Case("cmp.eq", "expression", "{{ inputs.scope == 'full' }}",
         _ctx(inputs={"scope": "full"}), value=True),
    Case("cmp.ne", "expression", "{{ steps.r.output.exit_code != 0 }}",
         _ctx(steps={"r": {"output": {"exit_code": 1}}}), value=True),
    Case("cmp.gt", "expression", "{{ steps.p.output.n > 5 }}",
         _ctx(steps={"p": {"output": {"n": 7}}}), value=True),
    Case("cmp.lt", "expression", "{{ steps.p.output.n < 5 }}",
         _ctx(steps={"p": {"output": {"n": 7}}}), value=False),
    Case("cmp.ge", "expression", "{{ inputs.n >= 5 }}", _ctx(inputs={"n": 5}), value=True),
    Case("cmp.le", "expression", "{{ inputs.n <= 5 }}", _ctx(inputs={"n": 5}), value=True),
    Case("cmp.numeric-string", "expression", "{{ inputs.v > '9' }}",
         _ctx(inputs={"v": "10"}), value=True),
    Case("cmp.lexicographic", "expression", "{{ inputs.name > 'alpha' }}",
         _ctx(inputs={"name": "beta"}), value=True),
    Case("cmp.incomparable", "expression", "{{ inputs.n > 'abc' }}",
         _ctx(inputs={"n": 5}), value=False),

    # --- boolean -----------------------------------------------------------
    Case("bool.and", "expression", "{{ inputs.a and inputs.b }}",
         _ctx(inputs={"a": True, "b": True}), value=True),
    Case("bool.or", "expression", "{{ inputs.a or inputs.b }}",
         _ctx(inputs={"a": False, "b": True}), value=True),
    Case("bool.not", "expression", "{{ not inputs.v }}", _ctx(inputs={"v": 0}), value=True),
    Case("bool.precedence", "expression", "{{ inputs.a or inputs.b and inputs.c }}",
         _ctx(inputs={"a": True, "b": False, "c": False}), value=True),
    Case("bool.parens", "expression", "{{ (inputs.a or inputs.b) and inputs.c }}",
         _ctx(inputs={"a": True, "b": False, "c": True}), value=True),

    # --- membership --------------------------------------------------------
    Case("mem.in-list", "expression", "{{ 'x' in inputs.items }}",
         _ctx(inputs={"items": ["x", "y"]}), value=True),
    Case("mem.not-in", "expression", "{{ 'z' not in inputs.items }}",
         _ctx(inputs={"items": ["x", "y"]}), value=True),
    Case("mem.in-quoted", "expression", "{{ 'cat' in 'cat and dog' }}", _ctx(), value=True),
    Case("mem.non-iterable", "expression", "{{ inputs.tag in inputs.count }}",
         _ctx(inputs={"tag": "x", "count": 5}), value=False),

    # --- literals ----------------------------------------------------------
    Case("lit.string", "expression", "{{ 'hello' }}", _ctx(), value="hello"),
    Case("lit.number", "expression", "{{ 42 }}", _ctx(), value=42),
    Case("lit.bool-lower", "expression", "{{ true }}", _ctx(), value=True),
    Case("lit.bool-upper", "expression", "{{ TRUE }}", _ctx(), value=True),
    Case("lit.null", "expression", "{{ null }}", _ctx(), value=None),
    Case("lit.list", "expression", "{{ [1, 2, 3] }}", _ctx(), value=[1, 2, 3]),
    Case("lit.list-quoted-comma", "expression", "{{ ['a, b', 'c'] }}", _ctx(), value=["a, b", "c"]),
    Case("lit.list-nested", "expression", "{{ [[1, 2], 3] }}", _ctx(), value=[[1, 2], 3]),
    Case("lit.list-trailing-comma", "expression", "{{ [1, 2,] }}", _ctx(), value=[1, 2]),
    Case("lit.too-many-spaces", "expression", "{{   inputs.n   }}",
         _ctx(inputs={"n": 5}), value=5),
    Case("lit.no-spaces", "expression", "{{inputs.n}}", _ctx(inputs={"n": 5}), value=5),

    # --- filters -----------------------------------------------------------
    Case("filter.default-none", "expression", "{{ inputs.missing | default('fb') }}",
         _ctx(), value="fb"),
    Case("filter.default-empty", "expression", "{{ inputs.empty | default('fb') }}",
         _ctx(inputs={"empty": ""}), value="fb"),
    Case("filter.default-list", "expression", "{{ inputs.missing | default([1, 2]) }}",
         _ctx(), value=[1, 2]),
    Case("filter.join", "expression", "{{ inputs.tags | join(', ') }}",
         _ctx(inputs={"tags": ["a", "b", "c"]}), value="a, b, c"),
    Case("filter.map", "expression", "{{ inputs.rows | map('id') }}",
         _ctx(inputs={"rows": [{"id": "a"}, {"id": "b"}]}), value=["a", "b"]),
    Case("filter.map-dot", "expression", "{{ inputs.rows | map('result.status') }}",
         _ctx(inputs={"rows": [{"result": {"status": "ok"}}, {"result": {"status": "bad"}}]}),
         value=["ok", "bad"]),
    Case("filter.contains-str", "expression", "{{ inputs.text | contains('world') }}",
         _ctx(inputs={"text": "hello world"}), value=True),
    Case("filter.contains-list", "expression", "{{ inputs.nums | contains(5) }}",
         _ctx(inputs={"nums": [1, 2, 5]}), value=True),
    Case("filter.from-json-obj", "expression", "{{ inputs.blob | from_json }}",
         _ctx(inputs={"blob": '{"n": 1}'}), value={"n": 1}),
    Case("filter.from-json-array", "expression", "{{ inputs.blob | from_json }}",
         _ctx(inputs={"blob": "[1, 2, 3]"}), value=[1, 2, 3]),
    Case("filter.from-json-number", "expression", "{{ inputs.blob | from_json }}",
         _ctx(inputs={"blob": "42"}), value=42),
    Case("filter.to-json", "expression", "{{ inputs.obj | to_json }}",
         _ctx(inputs={"obj": {"b": 2, "a": 1}}), value='{"a": 1, "b": 2}'),
    Case("filter.upper", "expression", "{{ inputs.s | upper }}",
         _ctx(inputs={"s": "Hello"}), value="HELLO"),
    Case("filter.lower", "expression", "{{ inputs.s | lower }}",
         _ctx(inputs={"s": "Hello"}), value="hello"),
    Case("filter.split", "expression", "{{ inputs.csv | split(',') }}",
         _ctx(inputs={"csv": "a,b,c"}), value=["a", "b", "c"]),
    Case("filter.length", "expression", "{{ inputs.items | length }}",
         _ctx(inputs={"items": ["a", "b", "c"]}), value=3),
    Case("filter.chain", "expression", "{{ inputs.rows | map('name') | join(', ') }}",
         _ctx(inputs={"rows": [{"name": "a"}, {"name": "b"}]}), value="a, b"),
    Case("filter.arg-path", "expression", "{{ inputs.tags | join(inputs.sep) }}",
         _ctx(inputs={"tags": ["a", "b"], "sep": "-"}), value="a-b"),
    Case("filter.pipe-in-arg", "expression", "{{ inputs.tags | join(' | ') }}",
         _ctx(inputs={"tags": ["a", "b"]}), value="a | b"),
    Case("filter.contains-brace", "expression", "{{ inputs.text | contains('{{') }}",
         _ctx(inputs={"text": "uses {{ jinja }} syntax"}), value=True),

    # --- errors ------------------------------------------------------------
    Case("err.unknown-filter", "expression", "{{ inputs.items | truncate }}",
         _ctx(inputs={"items": [1, 2, 3]}), expect="error"),
    Case("err.filter-unsupported-form", "expression", "{{ inputs.tags | join }}",
         _ctx(inputs={"tags": ["a", "b"]}), expect="error"),
    Case("err.filter-multi-arg", "expression", "{{ inputs.missing | default(1, 2) }}",
         _ctx(), expect="error"),
    Case("err.ambiguous-precedence", "expression", "{{ inputs.count > inputs.limit | default(5) }}",
         _ctx(inputs={"count": 10, "name": "x"}), expect="error"),
    Case("err.filter-after-not", "expression", "{{ not inputs.missing | default(1) }}",
         _ctx(inputs={"value": 0}), expect="error"),
    Case("err.from-json-invalid", "expression", "{{ inputs.blob | from_json }}",
         _ctx(inputs={"blob": "not json"}), expect="error"),
    Case("err.from-json-non-string", "expression", "{{ inputs.n | from_json }}",
         _ctx(inputs={"n": 3}), expect="error"),
    Case("err.upper-non-string", "expression", "{{ inputs.n | upper }}",
         _ctx(inputs={"n": 3}), expect="error"),
    Case("err.split-empty-sep", "expression", "{{ inputs.csv | split('') }}",
         _ctx(inputs={"csv": "a,b"}), expect="error"),
    Case("err.length-mapping", "expression", "{{ inputs.obj | length }}",
         _ctx(inputs={"obj": {"a": 1}}), expect="error"),
    Case("err.to-json-non-serializable", "expression", "{{ inputs.obj | to_json }}",
         _ctx(inputs={"obj": {"f": object()}}), expect="error"),
    Case("err.unbalanced-quote", "expression",
         "{{ inputs.name }} {{ inputs.missing | default('oops }}",
         _ctx(inputs={"name": "Bob"}), expect="error"),

    # --- condition semantics ----------------------------------------------
    Case("cond.truthy", "condition", "{{ inputs.ready }}",
         _ctx(inputs={"ready": True}), value=True),
    Case("cond.falsy-missing", "condition", "{{ inputs.missing }}", _ctx(), value=False),
    Case("cond.stdout-false", "condition", "{{ steps.check.output.stdout }}",
         _ctx(steps={"check": {"output": {"stdout": "false\n"}}}), value=False),
    Case("cond.stdout-true", "condition", "{{ steps.check.output.stdout }}",
         _ctx(steps={"check": {"output": {"stdout": " true "}}}), value=True),
    Case("cond.comparison", "condition", "{{ inputs.count > 100 }}",
         _ctx(inputs={"count": 5}), value=False),
    Case("cond.length-empty", "condition", "{{ inputs.items | length }}",
         _ctx(inputs={"items": []}), value=False),
    Case("cond.length-nonempty", "condition", "{{ inputs.items | length }}",
         _ctx(inputs={"items": ["a"]}), value=True),
    Case("cond.whitespace-only", "condition", "   ", _ctx(), value=True),
    Case("cond.plain-string", "condition", "falsey", _ctx(), value=True),
    Case("cond.empty-block", "condition", "{{ }}", _ctx(), value=False),
    Case("cond.choose", "condition", "{{ inputs.choose }}",
         _ctx(inputs={"choose": "approve"}), value=True),
]


ALL_CASES: list[Case] = REQUIRED_CASES + COMPAT_CASES
