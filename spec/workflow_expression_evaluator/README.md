# Workflow expression evaluator — library evaluation harness

Evaluation material for [Discussion #4917](https://github.com/github/spec-kit/discussions/4917).
Not intended for merge.

## What this branch contains

- `src/specify_cli/workflows/expressions/engine_*.py` — all candidate engines
  side by side (`engine.py` is wired to `engine_lark_a2`). Each candidate also
  has its own branch with only its engine wired in; see below.
- `harness/` — the comparison harness:
  - `corpus.py` — 96 declarative behavior cases. Tier `required` (7): every
    expression shape used by the shipped workflows (`speckit`, `bugfix`,
    `assess`). Tier `compat` (89): supported and tested, not used by shipped
    workflows. Error cases must raise `ValueError`; messages are not compared.
  - `backends/` — one adapter per engine; a backend whose dependency is
    missing is skipped.
  - `metrics.py` — engine-owned SLOC (engine module plus helpers only it
    uses, without docstrings/comments), cognitive complexity (complexipy),
    cyclomatic complexity and LLOC (radon), grammar size.
  - `bench.py` — indicative runtime per expression.
  - `run.py` — runs everything and writes `reports/report.md` and
    `reports/report.json`.
- `reports/`
  - `report.md`, `report.json` — generated matrix, metrics and runtime.
  - `raw-<candidate>-suite.txt` — full repository test suite with that
    candidate wired into `engine.py` (local paths replaced by `<repo>`).
  - `summary-4917-criteria.md` — results along the criteria from the
    discussion.

## Branches

| Branch | Content |
|---|---|
| `chore/workflow-expression-package` | evaluator split into a package; `engine.py` is the single selection point (behavior-neutral) |
| `chore/workflow-expression-gap-tests` | tests for implemented but untested behavior, on `main` |
| `chore/workflow-expression-eval-base` | package + gap tests: the base of every candidate |
| `chore/workflow-expression-lark` | Lark, Earley, first prototype |
| `chore/workflow-expression-lark-a2` | Lark, LALR, idiomatic — proposed |
| `chore/workflow-expression-pyparsing` | pyparsing |
| `chore/workflow-expression-jinja2` | Jinja2 |
| `chore/workflow-expression-liquid` | python-liquid |
| `chore/workflow-expression-harness` | this branch |

The candidate branches differ from `eval-base` only by their engine module,
one line in `engine.py` and the dependency in `pyproject.toml`; the Lark A2
branch additionally carries a behavior-neutral preparation commit and its
documented test changes.

## Reproduce

From the repository root:

```bash
uv sync --extra test
uv pip install --python .venv/bin/python \
  lark==1.3.1 pyparsing==3.3.3 jinja2==3.1.6 python-liquid==2.3.4 \
  complexipy==8.0.1 radon==6.0.1
.venv/bin/python spec/workflow_expression_evaluator/harness/run.py
```

Full suite for one candidate: check out its branch, then

```bash
.venv/bin/python -m pytest -q
```

Measured with Python 3.14.4 on Linux; runtime numbers vary between runs and
machines.
