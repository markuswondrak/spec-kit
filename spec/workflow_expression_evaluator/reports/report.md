# Expression evaluator: backend comparison

Corpus: 96 cases (7 required, 89 compat)

LOC = non-blank, non-comment lines of the engine module (docstrings included; legacy column). Use *Engine-owned code* below for size comparisons.

| Backend | LOC | required | compat | overall |
|---|---|---|---|---|
| current | 349 | 7/7 | 89/89 | 96/96 |
| lark | 349 | 7/7 | 87/89 | 94/96 |
| lark-a2 | 447 | 7/7 | 89/89 | 96/96 |
| jinja2 | 259 | 7/7 | 82/89 | 89/96 |
| pyparsing | 359 | 7/7 | 87/89 | 94/96 |
| liquid | 296 | 7/7 | 62/89 | 69/96 |

## Complexity metrics

Per source file, measured with `complexipy` (cognitive complexity) and `radon` (cyclomatic complexity, logical LOC). Grammar size is extracted from the source itself: Lark EBNF rule definitions and non-blank grammar lines; pyparsing node assignments and non-comment grammar-section lines. `-` means the tool or grammar is not applicable.

### Engine-owned code (the fair comparison)

Everything that is added or deleted together with one engine: the engine module plus shared-module helpers that only this engine uses (no other engine, no shared module, no sibling definition). SLOC excludes blank lines, comment-only lines and docstrings. The per-file table below does not see the helpers, so it understates the current engine.

| Engine | engine SLOC | exclusive helpers | helper SLOC | owned SLOC | cognitive sum | cognitive max |
|---|---|---|---|---|---|---|
| `engine_current.py` | 249 | `template.py:_split_top_level_commas`, `template.py:_is_single_list_literal`, `template.py:_is_wrapped_in_parens` | 64 | **313** | 166 | 70 |
| `engine_lark.py` | 295 | - | 0 | **295** | 87 | 36 |
| `engine_lark_a2.py` | 334 | - | 0 | **334** | 110 | 31 |
| `engine_jinja2.py` | 184 | - | 0 | **184** | 55 | 14 |
| `engine_pyparsing.py` | 305 | - | 0 | **305** | 87 | 36 |
| `engine_liquid.py` | 198 | - | 0 | **198** | 74 | 33 |

### Candidate engines (per file)

| Module | functions | cognitive sum | cognitive max | > 15 | cyclomatic sum | cyclomatic max | worst rank | LLOC | Grammar |
|---|---|---|---|---|---|---|---|---|---|
| `engine_current.py` | 9 | 125 | 70 | 2 | 86 | 38 | E | 223 | - |
| `engine_lark.py` | 31 | 87 | 36 | 2 | 95 | 24 | D | 236 | 12 rules / 47 lines |
| `engine_lark_a2.py` | 31 | 110 | 31 | 2 | 113 | 20 | C | 287 | 17 rules / 37 lines |
| `engine_jinja2.py` | 12 | 55 | 14 | 0 | 43 | 6 | B | 167 | - |
| `engine_pyparsing.py` | 14 | 87 | 36 | 2 | 74 | 24 | D | 247 | 22 nodes / 104 lines |
| `engine_liquid.py` | 14 | 74 | 33 | 1 | 42 | 8 | B | 195 | - |

### Shared package (identical for every candidate)

| Module | functions | cognitive sum | cyclomatic sum | LLOC |
|---|---|---|---|---|
| `__init__.py` | 0 | 0 | 0 | 8 |
| `api.py` | 2 | 6 | 6 | 21 |
| `diagnostics.py` | 10 | 52 | 54 | 129 |
| `engine.py` | 0 | 0 | 0 | 4 |
| `filters.py` | 11 | 56 | 42 | 127 |
| `namespace.py` | 3 | 26 | 20 | 50 |
| `operators.py` | 3 | 14 | 15 | 35 |
| `template.py` | 10 | 117 | 83 | 218 |
| **total** | 39 | 271 | 220 | 592 |

## Runtime (indicative)

`_evaluate_template` over 6 expressions x 300 rounds (harness/bench.py). `n/a` = the engine rejects one of the expressions, so it is not timed on a smaller set. Machine-dependent; compare the ratio.

| Engine | per expression | x current | first call | corpus | note |
|---|---|---|---|---|---|
| `engine_current` | 117 us | 1.0x | 0.1 ms | - |  |
| `engine_lark` | 3480 us | 29.7x | 2.0 ms | - |  |
| `engine_lark_a2` | 193 us | 1.6x | 0.2 ms | - |  |
| `engine_pyparsing` | 1114 us | 9.5x | 1.0 ms | - |  |
| `engine_jinja2` | - | - | - | - | n/a: '{{ steps.run-tests.output.exit_code == 0 }}' raises ValueError |
| `engine_liquid` | - | - | - | - | n/a: "{{ inputs.tags \| join(', ') }}" raises ValueError |
| `engine_lark (LALR probe)` | 105 us | 0.9x | 1.4 ms | 94/96 (diverging: err.ambiguous-precedence, err.filter-after-not) | parser='lalr' + keyword priority .2; `not in inputs.a` rejected (as current) |

## lark: divergences

- `[diff]` **err.ambiguous-precedence** (compat): expected error, got True
- `[diff]` **err.filter-after-not** (compat): expected error, got False

## jinja2: divergences

- `[raised]` **path.hyphen** (compat): ValueError: 'dict object' has no attribute 'run'; expected 1
- `[diff]` **cmp.numeric-string** (compat): got False, expected True
- `[raised]` **cmp.incomparable** (compat): TypeError: '>' not supported between instances of 'int' and 'str'; expected False
- `[raised]` **mem.non-iterable** (compat): TypeError: argument of type 'int' is not a container or iterable; expected False
- `[diff]` **lit.bool-upper** (compat): got None, expected True
- `[diff]` **err.ambiguous-precedence** (compat): expected error, got True
- `[diff]` **err.filter-after-not** (compat): expected error, got False

## pyparsing: divergences

- `[diff]` **err.ambiguous-precedence** (compat): expected error, got True
- `[diff]` **err.filter-after-not** (compat): expected error, got False

## liquid: divergences

- `[raised]` **interp.close-brace-arg** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.missing | default('}}') %}x{% endif %}' 1:21
  |
1 | {% if inputs.missing | default('}}') %}x{% endif %}
  |                      ^ expected end of expression, found '|'
; expected 'Bob: }}'
- `[raised]` **interp.open-brace-arg** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.missing | default('{{') %}x{% endif %}' 1:21
  |
1 | {% if inputs.missing | default('{{') %}x{% endif %}
  |                      ^ expected end of expression, found '|'
; expected 'Bob {{'
- `[diff]` **cmp.numeric-string** (compat): got False, expected True
- `[raised]` **cmp.incomparable** (compat): ValueError: '<' and '>' are not supported between 'str' and 'int'
  -> '{% if inputs.n > 'abc' %}x{% endif %}' 1:15
  |
1 | {% if inputs.n > 'abc' %}x{% endif %}
  |                ^ '<' and '>' are not supported between 'str' and 'int'
; expected False
- `[diff]` **bool.not** (compat): got False, expected True
- `[raised]` **mem.in-list** (compat): ValueError: expected end of expression, found in
  -> '{% if 'x' in inputs.items %}x{% endif %}' 1:10
  |
1 | {% if 'x' in inputs.items %}x{% endif %}
  |           ^^ expected end of expression, found in
; expected True
- `[raised]` **mem.not-in** (compat): ValueError: expected end of expression, found not
  -> '{% if 'z' not in inputs.items %}x{% endif %}' 1:10
  |
1 | {% if 'z' not in inputs.items %}x{% endif %}
  |           ^^^ expected end of expression, found not
; expected True
- `[raised]` **mem.in-quoted** (compat): ValueError: expected end of expression, found in
  -> '{% if 'cat' in 'cat and dog' %}x{% endif %}' 1:12
  |
1 | {% if 'cat' in 'cat and dog' %}x{% endif %}
  |             ^^ expected end of expression, found in
; expected True
- `[raised]` **mem.non-iterable** (compat): ValueError: expected end of expression, found in
  -> '{% if inputs.tag in inputs.count %}x{% endif %}' 1:17
  |
1 | {% if inputs.tag in inputs.count %}x{% endif %}
  |                  ^^ expected end of expression, found in
; expected False
- `[diff]` **lit.bool-upper** (compat): got None, expected True
- `[raised]` **lit.list** (compat): ValueError: missing or unexpected path segment
  -> '{% if [1, 2, 3] %}x{% endif %}' 1:7
  |
1 | {% if [1, 2, 3] %}x{% endif %}
  |        ^ missing or unexpected path segment
; expected [1, 2, 3]
- `[diff]` **lit.list-quoted-comma** (compat): got None, expected ['a, b', 'c']
- `[raised]` **lit.list-nested** (compat): ValueError: missing or unexpected path segment
  -> '{% if [[1, 2], 3] %}x{% endif %}' 1:8
  |
1 | {% if [[1, 2], 3] %}x{% endif %}
  |         ^ missing or unexpected path segment
; expected [[1, 2], 3]
- `[raised]` **lit.list-trailing-comma** (compat): ValueError: missing or unexpected path segment
  -> '{% if [1, 2,] %}x{% endif %}' 1:7
  |
1 | {% if [1, 2,] %}x{% endif %}
  |        ^ missing or unexpected path segment
; expected [1, 2]
- `[raised]` **filter.default-none** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.missing | default('fb') %}x{% endif %}' 1:21
  |
1 | {% if inputs.missing | default('fb') %}x{% endif %}
  |                      ^ expected end of expression, found '|'
; expected 'fb'
- `[raised]` **filter.default-empty** (compat): ValueError: expected an identifier, found empty
  -> '{% if inputs.empty | default('fb') %}x{% endif %}' 1:13
  |
1 | {% if inputs.empty | default('fb') %}x{% endif %}
  |              ^^^^^ expected an identifier, found empty
; expected 'fb'
- `[raised]` **filter.default-list** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.missing | default([1, 2]) %}x{% endif %}' 1:21
  |
1 | {% if inputs.missing | default([1, 2]) %}x{% endif %}
  |                      ^ expected end of expression, found '|'
; expected [1, 2]
- `[raised]` **filter.join** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.tags | join(', ') %}x{% endif %}' 1:18
  |
1 | {% if inputs.tags | join(', ') %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected 'a, b, c'
- `[raised]` **filter.map** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.rows | map('id') %}x{% endif %}' 1:18
  |
1 | {% if inputs.rows | map('id') %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected ['a', 'b']
- `[raised]` **filter.map-dot** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.rows | map('result.status') %}x{% endif %}' 1:18
  |
1 | {% if inputs.rows | map('result.status') %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected ['ok', 'bad']
- `[raised]` **filter.contains-str** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.text | contains('world') %}x{% endif %}' 1:18
  |
1 | {% if inputs.text | contains('world') %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected True
- `[raised]` **filter.contains-list** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.nums | contains(5) %}x{% endif %}' 1:18
  |
1 | {% if inputs.nums | contains(5) %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected True
- `[raised]` **filter.split** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.csv | split(',') %}x{% endif %}' 1:17
  |
1 | {% if inputs.csv | split(',') %}x{% endif %}
  |                  ^ expected end of expression, found '|'
; expected ['a', 'b', 'c']
- `[raised]` **filter.chain** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.rows | map('name') | join(', ') %}x{% endif %}' 1:18
  |
1 | {% if inputs.rows | map('name') | join(', ') %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected 'a, b'
- `[raised]` **filter.arg-path** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.tags | join(inputs.sep) %}x{% endif %}' 1:18
  |
1 | {% if inputs.tags | join(inputs.sep) %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected 'a-b'
- `[raised]` **filter.pipe-in-arg** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.tags | join(' | ') %}x{% endif %}' 1:18
  |
1 | {% if inputs.tags | join(' | ') %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected 'a | b'
- `[raised]` **filter.contains-brace** (compat): ValueError: expected end of expression, found '|'
  -> '{% if inputs.text | contains('{{') %}x{% endif %}' 1:18
  |
1 | {% if inputs.text | contains('{{') %}x{% endif %}
  |                   ^ expected end of expression, found '|'
; expected True
