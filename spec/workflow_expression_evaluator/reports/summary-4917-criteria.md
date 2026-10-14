# Workflow expression evaluator — candidates measured against the #4917 criteria

This summary compares every candidate on exactly the points raised in
[Discussion #4917](https://github.com/github/spec-kit/discussions/4917):

1. the five semantic constraints a replacement must account for,
2. the prototype requirements (compare against existing expression and
   condition cases, identify behavioral differences, show whether less code
   *and* maintenance remains),
3. the two different goals (Jinja2: outsource more of the evaluator;
   Lark/pyparsing: replace fragile parsing without changing the language).

Evidence: [`report.md`](report.md) / [`report.json`](report.json) (generated
by [`../harness/run.py`](../harness/run.py)) and the raw full-suite logs
`raw-<candidate>-suite.txt` in this directory. How to reproduce:
[`../README.md`](../README.md).

## Candidates and evidence

| ID | Candidate | What was built |
|---|---|---|
| current | hand-written parser (baseline) | — |
| A | Lark, Earley | first prototype; emulates the current engine's internals |
| **A2** | **Lark, LALR, idiomatic** | precedence guards in the grammar, `PATH` terminal derived from `namespace`, static tree walk instead of a probe run, parse errors routed into condition diagnostics, table-driven filters |
| C | pyparsing | 1:1 port of A |
| B | Jinja2 | `SandboxedEnvironment`, `compile_expression`, builtins replaced by our filter table |
| D | python-liquid | restricted environment, our filter table |
| — | simpleeval, cel-python | probed only (see end) |

Evidence per candidate, each wired into the single engine slot
(`expressions/engine.py`):

- **Corpus:** 96 declarative cases — 7 *required* (every shape used by the
  shipped workflows `speckit`, `bugfix`, `assess`) and 89 *compat* (supported
  and tested, not used by shipped workflows). Error cases must raise
  `ValueError`; messages are not compared.
- **Full repository test suite** (11,429 tests) against the wired engine.
- **Metrics:** engine-owned SLOC (engine module + helpers only it uses, no
  docstrings/comments), cognitive complexity (complexipy), cyclomatic
  complexity (radon), runtime per expression.

## 1. The five semantic constraints

Corpus cases grouped by constraint (passing / total):

| Constraint | current | A Lark | **A2 Lark** | C pyparsing | B Jinja2 | D liquid |
|---|---|---|---|---|---|---|
| Typed single-expression results (43 cases) | 43 | 43 | **43** | 43 | 38 | 24 |
| Missing-path handling (8) | 8 | 8 | **8** | 8 | 8 | 4 |
| Numeric-string / mixed comparisons (4) | 4 | 4 | **4** | 4 | **1** | **1** |
| Custom filter behavior (33) | 33 | 31 | **33** | 31 | 31 | 20 |
| Condition semantics (11) | 11 | 11 | **11** | 11 | 11 | 11 |
| Condition diagnostics (suite tests failing) | 0 | 16 | **0** | 16 | 24 | 25 |

How each candidate meets — or misses — each constraint:

**Typed single-expression results.** All candidates can return native values
for a lone `{{ … }}` (A/A2/C: own evaluator over the parse tree; B:
`compile_expression`; D: Liquid's typed output expression). The B and D
misses in this group are caused by the other constraints (hyphen paths,
comparisons, filter-call syntax, list literals), not by typing itself.

**Missing-path handling** (`None`, falsy in conditions, `default` fallback).
A/A2/C reuse the shared `namespace` resolver unchanged. B needs
`ChainableUndefined` plus normalization to `None` at the boundary and in front
of every filter. D handles undefined natively; its 4 misses are
`default('…')` calls, which Liquid only accepts as `default: '…'`.

**Numeric-string comparisons** (`'10' > '9'` is `True`; `5 > 'abc'` and
`'x' in 5` are `False`, not errors). A/A2/C reuse the shared `operators`
module. B and D compile operators to their own semantics: `'10' > '9'` is
`False` (lexical), the mixed cases raise `TypeError`/`ValueError`. Neither
library has an extension point for this; fixing it means a compiler/AST hack.
This is the exclusion point the discussion anticipated.

**Custom filter behavior** (10 filters, strict arity, unknown filters fail
loudly, filter on a comparison operand or after `not` is rejected as
ambiguous). A and C lose the two precedence guards: their grammars bind the
pipe tighter, so `count > limit | default(5)` returns a value instead of
raising. **A2 expresses the guards in the grammar** (a pipe chain only on an
operator-free head) and gets 33/33; its dispatcher is a lookup in a single
`FILTER_TABLE` (function, argument form, usage text). B loses the same two
guards; its builtins (`truncate`, `replace`, …) had to be removed so the
language stays closed, and strict arity needs a wrapper. D cannot express our
call syntax `name(arg)` at all.

**Condition diagnostics** (the gate that tells authors why a condition
cannot be evaluated). Not covered by the corpus (messages are not compared);
measured by the suite. A/C/B/D leave 16–25 diagnostics tests failing because
the diagnostics module still reasons about the old engine's internals. **A2
routes the parser's error (token and column) into the diagnostics** through a
`_syntax_error` hook and replaces the probe run with a static tree walk:
0 failures, three fixture rows now expect the parser's message.

## 2. Comparison against existing cases and behavioral differences

| | current | A | **A2** | C | B | D |
|---|---|---|---|---|---|---|
| Required tier (shipped workflows) | 7/7 | 7/7 | **7/7** | 7/7 | 7/7 | 7/7 |
| Compat tier | 89/89 | 87/89 | **89/89** | 87/89 | 82/89 | 62/89 |
| Suite failures (of 11,429) | 0 | 26 | **0** ¹ | 25 | 48 | 93 |
| Changed result for a **valid** expression | — | 0 | **0** | 0 | **5** corpus / 10 suite | **27** corpus / 66 suite |
| Accepts input that is invalid today | — | 2 guards | **0** | 2 guards | 2 guards | 0 |
| Rejects input that silently passed today | — | 2 | 2 | 2 | 1 ³ | 2 |

¹ After ten deliberate, documented test changes; suite of 11,427 (two tests
for removed probe internals deleted).
³ Jinja2 rejects the unclosed quote but evaluates `[1,2,3][1]` to `2`.

**Differences that change the language (exclusion).**
- **B (Jinja2):** `steps.run-tests.output…` parses as subtraction — hyphenated
  step IDs are supported, tested syntax (not used by the three shipped
  workflows, which is why the required tier stays 7/7); `'10' > '9'` is lexical; mixed comparisons and
  `in` on a non-iterable raise; `TRUE` is undefined. A similar syntax does
  not mean equivalent behavior.
- **D (liquid):** filter calls (`| join(', ')` vs `| join: ', '`), `in` /
  `not in`, list literals, `not 0`, comparison semantics; reserved path
  segments (`empty`, `contains`, …) are parse errors.

**Differences every parser-based candidate shares (unavoidable).** They are
the same for A, A2, B, C, D, and they make the evaluator stricter, never
looser:
- `[1,2,3][1]` used to return `None` silently, an unclosed quote in a
  condition used to evaluate to `False` silently — both now raise
  `ValueError` (Jinja2 reads the first as `2`).
- Some error messages come from the parser (`unexpected token … at column N`)
  instead of the hand-written dispatcher; still `ValueError`.
- Two tests that monkeypatch internals of the current engine are replaced.

For A2 these are executed as ten test changes, each listed with its reason in
the A2 commit message and in the changed tests themselves. They are the sign-off list for adopting any
parser library.

## 3. Less code and maintenance?

| | current | A | **A2** | C | B | D |
|---|---|---|---|---|---|---|
| Engine-owned SLOC | 313 | 295 | **334 (+7 %)** | 305 | 184 | 198 |
| Cognitive complexity Σ | 166 | 87 | **110 (−34 %)** | 87 | 55 | 74 |
| Hardest function (cognitive) | 70 | 36 | **31 (−56 %)** | 36 | 14 | 33 |
| Hardest function (cyclomatic, rank) | 38 (E) | 24 (D) | **20 (C)** | 24 (D) | 6 (B) | 8 (B) |
| Worst cyclomatic rank | E | D | **C** | D | B | B |
| Grammar | — | 47 lines | **37 lines** | 104 lines (Python) | — | — |
| Runtime vs current | 1× | 25–30× (Earley) | **1.3–1.6×** | 9–10× | n/a ² | n/a ² |
| New runtime dependency | — | `lark` | `lark` | `pyparsing` | `jinja2`, `markupsafe` | `python-liquid` + 5 transitive |
| Test changes to adopt | — | ~10 | **10 (done)** | ~10 | excluded | excluded |

² B and D reject parts of the benchmark set (hyphen path, `| join(…)`).

Unchanged for every candidate: template scanner, namespace resolver, filter
implementations, operator semantics and diagnostics remain our code (shared
package: 1,311 lines, 592 logical).

**Code.** Only the excluded engines shrink substantially. A2 is the same size
as today (+21 SLOC) but simpler: complexity −34 %, hardest function 70 → 31.
The +21 lines buy static primitives and parse-error diagnostics that A got
"for free" only by re-running the evaluator as a probe.

**Maintenance.** The structural risk moves out of our code:
- Of 22 fixes to the evaluator since April 2026, **11 were structural parser
  bugs** (quote-awareness, whitespace, parentheses, precedence, list
  literals, filter chains, trailing tokens, multi-argument calls); the latest
  one landed 2026-10-01. A tokenizer + grammar removes this class by
  construction. (Classified by commit title.)
- A second syntax definition is avoided: A2 derives its path terminal from
  `namespace`, and the existing drift test now covers the grammar too.
- Adding a filter becomes its function plus one `FILTER_TABLE` row; the
  registry, arity check and "expected one of …" message derive from the
  table (today: regex dispatch, registry entry and message by hand).
- Cost: a pure-Python dependency without transitive dependencies (`lark`
  1.3.1), parser-generated error messages for workflow authors, and grammar
  knowledge in the team.

## 4. The two goals from the discussion

| Goal | Result |
|---|---|
| Jinja2 — outsource more of the evaluator | Achieved in size (−41 % owned SLOC), not in behavior: hyphenated paths and numeric-string comparison break, and neither has an extension point. Excluded. |
| Lark — remove fragile parsing without changing the workflow language | **Achieved by A2:** 96/96 corpus, 0 suite failures, 0 changed results for valid expressions, runtime 1.3–1.6×. Only the stricter-rejection changes above. |
| pyparsing — same strategy as Lark | Same behavior as A, 9–10× slower, grammar 104 lines of Python. Fallback option, no advantage. |

Probed only: **simpleeval** (hyphen paths fail, `|` is bitwise OR,
`'10' > '9'` is `False`, `TRUE` unknown) and **cel-python** (hyphen paths
fail, no pipe filters, `&&`/`||`/`!` syntax, strict typing) — both would be a
language migration, as the discussion expected.

## Caveats

- The compat tier encodes today's behavior including its workarounds; 96/96
  for `current` is circular by construction.
- A/B/C/D suite runs used the unmodified suite (11,429 tests); A2 ran against
  the suite with its ten documented test changes.
- Runtime numbers are indicative (single machine, three harness runs).
- Maintenance history is classified by commit title, not by diff.

## Open before a recommendation

- Maintainer sign-off on the ten test changes (stricter rejections, parser
  messages, replaced internals tests).
- Error-message policy for workflow authors (parser messages are English and
  name the column).
- Dependency/security note for `lark`.
