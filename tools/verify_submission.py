"""Verify a delegated task submission against its brief.

Usage:
    uv run python tools/verify_submission.py T-001 [--base main] [--no-gates]

The brief lives at ``docs/tasks/<TASK_ID>.md`` and starts with TOML front
matter between ``+++`` lines (see ``docs/tasks/README.md``). The script checks:

1. Scope: every changed file matches the brief's ``allowed_paths``.
2. Protection: no changed file matches the brief's ``protected_paths`` or the
   global protected list (contracts, acceptance tests, governance, manifests).
3. Stubs: added lines contain no TODO markers, skipped tests, ``todo!()``,
   ``NotImplementedError`` or pass-only function bodies.
4. Gates: lint, format, type check, Rust tests, Python tests and the brief's
   acceptance command all pass.

It prints a compact report and exits 0 only when every check passes. A run
with ``--no-gates`` can never verify a task; it only gives early feedback.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files no delegated task may change. Dependency manifests are included because
# every new dependency needs an architect-reviewed licence check.
GLOBAL_PROTECTED = (
    "CLAUDE.md",
    "AGENTS.md",
    "LICENSE",
    "docs/adr/**",
    "docs/tasks/**",
    "docs/ARCHITECTURE.md",
    "docs/ROADMAP.md",
    "project-status.json",
    "tools/verify_submission.py",
    "tests/python/test_verify_submission.py",
    ".github/**",
    "pyproject.toml",
    "uv.lock",
    "Cargo.toml",
    "Cargo.lock",
    "crates/*/Cargo.toml",
    "deny.toml",
    "rust-toolchain.toml",
)

STUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("marker", re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")),
    ("not-implemented", re.compile(r"\bNotImplementedError\b")),
    ("skipped-test", re.compile(r"pytest\.(mark\.)?(skip|skipif|xfail)\b|unittest\.skip")),
    ("rust-stub", re.compile(r"\b(todo|unimplemented)!\s*\(")),
    ("rust-ignored-test", re.compile(r"#\[ignore\b")),
)

PASS_ONLY_EXEMPT_DECORATORS = {"abstractmethod", "overload"}


@dataclass(frozen=True)
class Brief:
    """Machine-readable part of a task brief."""

    task_id: str
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    acceptance: str


@dataclass
class Report:
    """Collected check results."""

    lines: list[str] = field(default_factory=list)
    failures: int = 0

    def add(self, ok: bool, name: str, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        self.lines.append(f"[{status}] {name}" + (f": {detail}" if detail else ""))
        if not ok:
            self.failures += 1

    def note(self, text: str) -> None:
        self.lines.append(f"[NOTE] {text}")


def parse_brief(text: str, task_id: str) -> Brief:
    """Parse the TOML front matter of a brief."""
    match = re.match(r"\A\+\+\+\s*\n(.*?)\n\+\+\+\s*(\n|\Z)", text, re.DOTALL)
    if match is None:
        raise ValueError("brief must start with TOML front matter between '+++' lines")
    data: dict[str, Any] = tomllib.loads(match.group(1))
    if data.get("id") != task_id:
        raise ValueError(f"brief id {data.get('id')!r} does not match {task_id!r}")
    allowed = tuple(str(p) for p in data.get("allowed_paths", []))
    if not allowed:
        raise ValueError("brief must list at least one allowed path")
    acceptance = str(data.get("acceptance", "")).strip()
    if not acceptance:
        raise ValueError("brief must define an acceptance command")
    return Brief(
        task_id=task_id,
        allowed_paths=allowed,
        protected_paths=tuple(str(p) for p in data.get("protected_paths", [])),
        acceptance=acceptance,
    )


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a repository glob (``*``, ``?``, ``**``) to a regex on POSIX paths."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    """Return True when ``path`` matches at least one glob."""
    return any(glob_to_regex(p).match(path) for p in patterns)


def find_stub_lines(path: str, added_lines: list[tuple[int, str]]) -> list[str]:
    """Return findings for stub patterns in added lines of ``path``."""
    findings: list[str] = []
    for lineno, line in added_lines:
        for name, pattern in STUB_PATTERNS:
            if pattern.search(line):
                findings.append(f"{path}:{lineno} {name}: {line.strip()[:80]}")
    return findings


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _is_protocol(cls: ast.ClassDef) -> bool:
    return any(_decorator_name(base) == "Protocol" for base in cls.bases)


def _is_pass_only(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(fn.body)
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]
    if not body:
        return True
    if len(body) != 1:
        return False
    stmt = body[0]
    return isinstance(stmt, ast.Pass) or (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def find_pass_only_functions(path: str, source: str) -> list[str]:
    """Return functions whose body is only ``pass``, ``...`` or a docstring.

    Protocol members, abstract methods and overloads are exempt; they are
    interface declarations, not implementations.
    """
    tree = ast.parse(source, filename=path)
    findings: list[str] = []

    def visit(node: ast.AST, in_protocol: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, _is_protocol(child))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                exempt = in_protocol or any(
                    _decorator_name(d) in PASS_ONLY_EXEMPT_DECORATORS for d in child.decorator_list
                )
                if not exempt and _is_pass_only(child):
                    findings.append(f"{path}:{child.lineno} pass-only function {child.name}()")
                visit(child, in_protocol=False)

    visit(tree, in_protocol=False)
    return findings


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout


def changed_files(base: str) -> tuple[str, list[str]]:
    """Return the merge base and every file changed since it (tracked or untracked)."""
    merge_base = _git("merge-base", base, "HEAD").strip()
    tracked = _git("diff", "--name-only", merge_base).split()
    untracked = _git("ls-files", "--others", "--exclude-standard").split()
    return merge_base, sorted(set(tracked) | set(untracked))


def added_lines(merge_base: str, path: str) -> list[tuple[int, str]]:
    """Return (line number, text) of lines added to ``path`` since ``merge_base``."""
    file_path = REPO_ROOT / path
    if not file_path.exists():
        return []
    if not _git("ls-files", path).strip():
        text = file_path.read_text(encoding="utf-8", errors="replace")
        return list(enumerate(text.splitlines(), start=1))
    diff = _git("diff", "-U0", merge_base, "--", path)
    lines: list[tuple[int, str]] = []
    lineno = 0
    for raw in diff.splitlines():
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            lineno = int(hunk.group(1))
        elif raw.startswith("+") and not raw.startswith("+++"):
            lines.append((lineno, raw[1:]))
            lineno += 1
    return lines


def gate_commands(brief: Brief) -> list[tuple[str, list[str] | str]]:
    """Quality gates in execution order; a string runs through the shell."""
    py = sys.executable
    return [
        ("ruff check", [py, "-m", "ruff", "check", "."]),
        ("ruff format", [py, "-m", "ruff", "format", "--check", "."]),
        ("mypy", [py, "-m", "mypy"]),
        ("cargo fmt", ["cargo", "fmt", "--all", "--check"]),
        (
            "cargo clippy",
            ["cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"],
        ),
        ("cargo test", ["cargo", "test", "--workspace"]),
        ("pytest", [py, "-m", "pytest", "-q"]),
        ("acceptance", brief.acceptance),
    ]


def run_gate(command: list[str] | str) -> tuple[bool, str]:
    """Run one gate and return (passed, last output line)."""
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        shell=isinstance(command, str),
        check=False,
    )
    output = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode == 0, (output[-1][:120] if output else "")


def verify(task_id: str, base: str, *, run_gates: bool) -> Report:
    """Run every check for ``task_id`` and return the report."""
    report = Report()
    brief_path = REPO_ROOT / "docs" / "tasks" / f"{task_id}.md"
    try:
        brief = parse_brief(brief_path.read_text(encoding="utf-8"), task_id)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        report.add(False, "brief", str(exc))
        return report

    merge_base, files = changed_files(base)
    report.note(f"{len(files)} changed file(s) since {base} ({merge_base[:10]})")

    outside = [f for f in files if not matches_any(f, brief.allowed_paths)]
    report.add(not outside, "scope", ", ".join(outside) or "all files within allowed paths")

    protected = [f for f in files if matches_any(f, GLOBAL_PROTECTED + brief.protected_paths)]
    report.add(not protected, "protected", ", ".join(protected) or "untouched")

    stubs: list[str] = []
    for f in files:
        lines = added_lines(merge_base, f)
        stubs.extend(find_stub_lines(f, lines))
        if f.endswith(".py") and (REPO_ROOT / f).exists():
            try:
                stubs.extend(
                    find_pass_only_functions(f, (REPO_ROOT / f).read_text(encoding="utf-8"))
                )
            except SyntaxError as exc:
                stubs.append(f"{f}: syntax error: {exc.msg}")
    report.add(not stubs, "stubs", "; ".join(stubs[:10]) or "none found")

    if not run_gates:
        report.add(False, "gates", "skipped (--no-gates); a task cannot be verified this way")
        return report
    for name, command in gate_commands(brief):
        ok, last = run_gate(command)
        report.add(ok, name, "" if ok else last)
    report.note("hardcoded expected values cannot be detected automatically; review the diff")
    return report


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("task_id", help="task identifier, e.g. T-001")
    parser.add_argument("--base", default="main", help="branch the submission is based on")
    parser.add_argument("--no-gates", action="store_true", help="skip lint/type/test gates")
    args = parser.parse_args(argv)

    report = verify(args.task_id, args.base, run_gates=not args.no_gates)
    print(f"verify {args.task_id}")
    for line in report.lines:
        print(line)
    verdict = "PASS" if report.failures == 0 else f"FAIL ({report.failures} check(s) failed)"
    print(f"RESULT: {verdict}")
    return 0 if report.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
