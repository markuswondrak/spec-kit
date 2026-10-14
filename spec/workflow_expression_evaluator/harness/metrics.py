"""S5 complexity metrics for the candidate engines and the shared package.

Measures every engine module and every shared package file with ``complexipy``
(cognitive complexity) and ``radon`` (cyclomatic complexity, logical LOC) and
extracts the grammar size for the parser-generator engines. Both tools are
optional: when one is missing, its columns report ``"-"`` instead of failing
the harness.

``run.py`` appends the result to ``report.md`` and ``report.json``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_PACKAGE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "specify_cli"
    / "workflows"
    / "expressions"
)

# The candidate surface: every engine that can be swapped in through engine.py,
# including the current one as the baseline.
_ENGINE_MODULES = (
    "engine_current.py",
    "engine_lark.py",
    "engine_lark_a2.py",
    "engine_jinja2.py",
    "engine_pyparsing.py",
    "engine_liquid.py",
)

# Identical for every candidate; listed so the report shows what never moves.
_SHARED_MODULES = (
    "__init__.py",
    "api.py",
    "diagnostics.py",
    "engine.py",
    "filters.py",
    "namespace.py",
    "operators.py",
    "template.py",
)

# Engines whose source carries a grammar; the payload is counted, not measured
# by the Python metrics.
_GRAMMAR_MODULES = {
    "engine_lark.py": "rules",
    "engine_lark_a2.py": "rules",
    "engine_pyparsing.py": "nodes",
}

# Shared leaf modules whose helpers may in fact belong to a single engine.
_LEAF_MODULES = ("namespace.py", "filters.py", "operators.py", "template.py")


@dataclass
class OwnedMetrics:
    """Code that is added or deleted together with one engine."""

    module: str
    engine_sloc: int = 0
    helper_sloc: int = 0
    owned_sloc: int = 0
    cognitive_sum: int | None = None
    cognitive_max: int | None = None
    cyclomatic_sum: int | None = None
    cyclomatic_max: int | None = None
    cyclomatic_rank: str | None = None
    helpers: list[str] = field(default_factory=list)


@dataclass
class FileMetrics:
    module: str
    group: str  # "engine" | "shared"
    functions: int | None = None
    cognitive_sum: int | None = None
    cognitive_max: int | None = None
    cognitive_over_15: int | None = None
    cyclomatic_sum: int | None = None
    cyclomatic_max: int | None = None
    worst_rank: str | None = None
    lloc: int | None = None
    grammar_kind: str | None = None
    grammar_items: int | None = None
    grammar_lines: int | None = None


def _cognitive(path: Path) -> dict[str, int] | None:
    """Cognitive complexity per function and in total, or ``None`` if missing."""
    try:
        from complexipy import file_complexity
    except ImportError:
        return None
    report = file_complexity(str(path), no_ignore=True)
    scores = [function.complexity for function in report.functions]
    return {
        "functions": len(scores),
        "sum": report.complexity,
        "max": max(scores, default=0),
        "over_15": sum(1 for score in scores if score > 15),
    }


def _cyclomatic(path: Path) -> dict[str, int | str] | None:
    """Cyclomatic complexity and LLOC via radon, or ``None`` if missing."""
    try:
        from radon.complexity import cc_rank, cc_visit
        from radon.raw import analyze
    except ImportError:
        return None
    source = path.read_text(encoding="utf-8")
    blocks = cc_visit(source)
    scores = [block.complexity for block in blocks]
    ranks = [cc_rank(block.complexity) for block in blocks]
    return {
        "sum": sum(scores),
        "max": max(scores, default=0),
        "worst_rank": max(ranks, default="A"),
        "lloc": analyze(source).lloc,
    }


def _grammar(path: Path, module: str) -> tuple[int | None, int | None]:
    """Grammar payload of *module*: (items, non-blank lines), or ``(None, None)``.

    Lark: EBNF rule definitions (``name:``/``?name:``) inside ``_GRAMMAR``
    (``engine_lark.py``) or the ``_grammar()`` template parts
    (``engine_lark_a2.py``). pyparsing: node assignments in the
    ``# -- Grammar`` section.
    """
    text = path.read_text(encoding="utf-8")
    if module in ("engine_lark.py", "engine_lark_a2.py"):
        if module == "engine_lark.py":
            match = re.search(r'_GRAMMAR = r"""(.*?)"""', text, re.S)
            if match is None:
                return None, None
            grammar_text = match.group(1)
        else:
            parts = re.findall(r'r"""(.*?)"""', text, re.S)
            if not parts:
                return None, None
            grammar_text = "".join(parts)
        body = [line for line in grammar_text.splitlines() if line.strip()]
        items = sum(1 for line in body if re.match(r"^\??[_a-z][\w]*\s*:", line))
        return items, len(body)
    if module == "engine_pyparsing.py":
        section = text.split("# -- Grammar", 1)[-1].split("# -- Evaluation", 1)[0]
        body = [
            line
            for line in section.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        items = sum(1 for line in body if re.match(r"^    [a-z_]+ (?:=|\<<=) ", line))
        return items, len(body)
    return None, None


def _cognitive_by_name(path: Path) -> dict[str, int]:
    """Cognitive complexity per top-level name (methods folded into their class)."""
    try:
        from complexipy import file_complexity
    except ImportError:
        return {}
    scores: dict[str, int] = {}
    for function in file_complexity(str(path), no_ignore=True).functions:
        top = function.name.split("::")[0]
        scores[top] = scores.get(top, 0) + function.complexity
    return scores


def _cyclomatic_by_name(path: Path) -> dict[str, int]:
    """Cyclomatic complexity per top-level name (methods folded into their class)."""
    try:
        from radon.complexity import cc_visit
    except ImportError:
        return {}
    scores: dict[str, int] = {}
    for block in cc_visit(path.read_text(encoding="utf-8")):
        top = block.name.split(".")[0]
        scores[top] = scores.get(top, 0) + block.complexity
    return scores


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    return lines


def _sloc_by_name(path: Path) -> tuple[int, dict[str, int]]:
    """Source lines without blanks, comment-only lines and docstrings.

    Returns the module total and the count per top-level name. Docstrings are
    excluded because the current engine carries its bug history in them; a
    fresh prototype would otherwise look small for the wrong reason.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docs = _docstring_lines(tree)
    lines = source.splitlines()

    def count(start: int, end: int) -> int:
        return sum(
            1
            for number in range(start, end + 1)
            if number not in docs
            and lines[number - 1].strip()
            and not lines[number - 1].lstrip().startswith("#")
        )

    per_name: dict[str, int] = {}
    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name is not None:
            per_name[name] = count(node.lineno, node.end_lineno)
    return count(1, len(lines)), per_name


def _relative_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level
        for alias in node.names
    }


def _intra_module_uses(path: Path) -> set[str]:
    """Top-level names one top-level definition of *path* uses from another."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in tree.body:
        own = getattr(node, "name", None)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id != own:
                used.add(sub.id)
    return used


def _exclusive_helpers() -> dict[str, list[tuple[str, str]]]:
    """Shared-module helpers that only one engine uses, per engine module.

    A helper counts as engine-owned when exactly one engine imports it, no
    shared module imports it, and no other definition in its own module uses
    it -- it is deleted together with that engine. Today these are the
    current engine's parser helpers that the 2.2 split placed in ``template``.
    """
    engines = [m for m in _ENGINE_MODULES if (_PACKAGE / m).exists()]
    engine_imports = {m: _relative_imports(_PACKAGE / m) for m in engines}
    shared_imports: set[str] = set()
    for module in _SHARED_MODULES:
        if (_PACKAGE / module).exists():
            shared_imports |= _relative_imports(_PACKAGE / module)

    owned: dict[str, list[tuple[str, str]]] = {m: [] for m in engines}
    for module in _LEAF_MODULES:
        path = _PACKAGE / module
        _, per_name = _sloc_by_name(path)
        intra = _intra_module_uses(path)
        for name in per_name:
            users = [m for m in engines if name in engine_imports[m]]
            if len(users) == 1 and name not in shared_imports and name not in intra:
                owned[users[0]].append((module, name))
    return owned


def measure_owned() -> list[OwnedMetrics]:
    """Engine-owned code: the engine module plus its exclusive shared helpers."""
    helpers = _exclusive_helpers()
    result: list[OwnedMetrics] = []
    for module, extra in helpers.items():
        path = _PACKAGE / module
        sloc, _ = _sloc_by_name(path)
        cognitive = _cognitive(path) or {}
        cyclomatic = _cyclomatic(path) or {}
        entry = OwnedMetrics(
            module=module,
            engine_sloc=sloc,
            cognitive_sum=cognitive.get("sum"),
            cognitive_max=cognitive.get("max"),
            cyclomatic_sum=cyclomatic.get("sum"),
            cyclomatic_max=cyclomatic.get("max"),
        )
        for helper_module, name in extra:
            helper_path = _PACKAGE / helper_module
            _, per_name = _sloc_by_name(helper_path)
            score = _cognitive_by_name(helper_path).get(name, 0)
            cc_score = _cyclomatic_by_name(helper_path).get(name, 0)
            entry.helpers.append(f"{helper_module}:{name}")
            entry.helper_sloc += per_name[name]
            if entry.cognitive_sum is not None:
                entry.cognitive_sum += score
                entry.cognitive_max = max(entry.cognitive_max or 0, score)
            if entry.cyclomatic_sum is not None:
                entry.cyclomatic_sum += cc_score
                entry.cyclomatic_max = max(entry.cyclomatic_max or 0, cc_score)
        if entry.cyclomatic_max is not None:
            try:
                from radon.complexity import cc_rank
                entry.cyclomatic_rank = cc_rank(entry.cyclomatic_max)
            except ImportError:
                entry.cyclomatic_rank = None
        entry.owned_sloc = entry.engine_sloc + entry.helper_sloc
        result.append(entry)
    return result


def measure() -> list[FileMetrics]:
    """Measure every existing engine and shared package file."""
    metrics: list[FileMetrics] = []
    for group, modules in (("engine", _ENGINE_MODULES), ("shared", _SHARED_MODULES)):
        for module in modules:
            path = _PACKAGE / module
            if not path.exists():
                continue
            entry = FileMetrics(module=module, group=group)
            cognitive = _cognitive(path)
            if cognitive:
                entry.functions = cognitive["functions"]
                entry.cognitive_sum = cognitive["sum"]
                entry.cognitive_max = cognitive["max"]
                entry.cognitive_over_15 = cognitive["over_15"]
            cyclomatic = _cyclomatic(path)
            if cyclomatic:
                entry.cyclomatic_sum = int(cyclomatic["sum"])
                entry.cyclomatic_max = int(cyclomatic["max"])
                entry.worst_rank = str(cyclomatic["worst_rank"])
                entry.lloc = int(cyclomatic["lloc"])
            if module in _GRAMMAR_MODULES:
                entry.grammar_kind = _GRAMMAR_MODULES[module]
                entry.grammar_items, entry.grammar_lines = _grammar(path, module)
            metrics.append(entry)
    return metrics


def _cell(value: object) -> str:
    return "-" if value is None else str(value)


def _grammar_cell(entry: FileMetrics) -> str:
    if entry.grammar_kind is None:
        return "-"
    return f"{_cell(entry.grammar_items)} {entry.grammar_kind} / {_cell(entry.grammar_lines)} lines"


def write_section(files: list[FileMetrics], owned: list[OwnedMetrics] | None = None) -> list[str]:
    """Markdown lines for the S5 complexity section."""
    lines = [
        "## Complexity metrics",
        "",
        "Per source file, measured with `complexipy` (cognitive complexity) and "
        "`radon` (cyclomatic complexity, logical LOC). Grammar size is extracted "
        "from the source itself: Lark EBNF rule definitions and non-blank grammar "
        "lines; pyparsing node assignments and non-comment grammar-section lines. "
        "`-` means the tool or grammar is not applicable.",
        "",
    ]
    if owned:
        lines += [
            "### Engine-owned code (the fair comparison)",
            "",
            "Everything that is added or deleted together with one engine: the "
            "engine module plus shared-module helpers that only this engine uses "
            "(no other engine, no shared module, no sibling definition). SLOC "
            "excludes blank lines, comment-only lines and docstrings. The "
            "per-file table below does not see the helpers, so it understates "
            "the current engine.",
            "",
            "| Engine | engine SLOC | exclusive helpers | helper SLOC | owned SLOC | "
            "cognitive sum | cognitive max | cyclomatic sum | cyclomatic max (rank) |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for entry in owned:
            helpers = ", ".join(f"`{h}`" for h in entry.helpers) or "-"
            rank = f" ({entry.cyclomatic_rank})" if entry.cyclomatic_rank else ""
            lines.append(
                f"| `{entry.module}` | {entry.engine_sloc} | {helpers} | "
                f"{entry.helper_sloc} | **{entry.owned_sloc}** | "
                f"{_cell(entry.cognitive_sum)} | {_cell(entry.cognitive_max)} | "
                f"{_cell(entry.cyclomatic_sum)} | {_cell(entry.cyclomatic_max)}{rank} |"
            )
        lines.append("")
    lines += [
        "### Candidate engines (per file)",
        "",
        "| Module | functions | cognitive sum | cognitive max | > 15 | "
        "cyclomatic sum | cyclomatic max | worst rank | LLOC | Grammar |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in files:
        if entry.group != "engine":
            continue
        lines.append(
            f"| `{entry.module}` | {_cell(entry.functions)} | {_cell(entry.cognitive_sum)} | "
            f"{_cell(entry.cognitive_max)} | {_cell(entry.cognitive_over_15)} | "
            f"{_cell(entry.cyclomatic_sum)} | {_cell(entry.cyclomatic_max)} | "
            f"{_cell(entry.worst_rank)} | {_cell(entry.lloc)} | {_grammar_cell(entry)} |"
        )
    lines.append("")
    lines.append("### Shared package (identical for every candidate)")
    lines.append("")
    lines.append("| Module | functions | cognitive sum | cyclomatic sum | LLOC |")
    lines.append("|---|---|---|---|---|")
    for entry in files:
        if entry.group != "shared":
            continue
        lines.append(
            f"| `{entry.module}` | {_cell(entry.functions)} | {_cell(entry.cognitive_sum)} | "
            f"{_cell(entry.cyclomatic_sum)} | {_cell(entry.lloc)} |"
        )
    shared = [entry for entry in files if entry.group == "shared"]

    def total(attr: str) -> int | None:
        values = [getattr(e, attr) for e in shared if getattr(e, attr) is not None]
        return sum(values) if values else None

    lines.append(
        f"| **total** | {_cell(total('functions'))} | {_cell(total('cognitive_sum'))} | "
        f"{_cell(total('cyclomatic_sum'))} | {_cell(total('lloc'))} |"
    )
    lines.append("")
    return lines
