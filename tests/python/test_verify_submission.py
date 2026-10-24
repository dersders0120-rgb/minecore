"""Tests for tools/verify_submission.py (pure functions only)."""

from __future__ import annotations

import pytest

import verify_submission as vs

BRIEF = """+++
id = "T-007"
title = "CSV collar importer"
owner = "deepseek"
allowed_paths = ["python/minecore/io/**", "tests/python/io/test_extra_*.py"]
protected_paths = ["tests/python/io/test_acceptance.py"]
acceptance = "python -m pytest tests/python/io -q"
+++

# Body
"""


def test_parse_brief_reads_front_matter() -> None:
    brief = vs.parse_brief(BRIEF, "T-007")
    assert brief.allowed_paths == ("python/minecore/io/**", "tests/python/io/test_extra_*.py")
    assert brief.protected_paths == ("tests/python/io/test_acceptance.py",)
    assert brief.acceptance == "python -m pytest tests/python/io -q"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# no front matter", "front matter"),
        (
            BRIEF.replace(
                'allowed_paths = ["python/minecore/io/**", "tests/python/io/test_extra_*.py"]', ""
            ),
            "allowed path",
        ),
        (BRIEF.replace('acceptance = "python -m pytest tests/python/io -q"', ""), "acceptance"),
    ],
)
def test_parse_brief_rejects_incomplete_briefs(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        vs.parse_brief(text, "T-007")


def test_parse_brief_rejects_mismatched_id() -> None:
    with pytest.raises(ValueError, match="does not match"):
        vs.parse_brief(BRIEF, "T-008")


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("python/minecore/io/csv.py", "python/minecore/io/**", True),
        ("python/minecore/io/sub/x.py", "python/minecore/io/**", True),
        ("python/minecore/cli.py", "python/minecore/io/**", False),
        ("tests/python/io/test_extra_1.py", "tests/python/io/test_extra_*.py", True),
        ("tests/python/io/deep/test_extra_1.py", "tests/python/io/test_extra_*.py", False),
        ("crates/mc-core/Cargo.toml", "crates/*/Cargo.toml", True),
        ("Cargo.toml", "crates/*/Cargo.toml", False),
        ("docs/adr/0001-x.md", "docs/adr/**", True),
        ("a/b/c.rs", "**/c.rs", True),
        ("c.rs", "**/c.rs", True),
        ("CLAUDE.md", "CLAUDE.md", True),
        ("CLAUDExmd", "CLAUDE.md", False),
    ],
)
def test_glob_matching(path: str, pattern: str, expected: bool) -> None:
    assert vs.matches_any(path, (pattern,)) is expected


def test_global_protection_covers_manifests_and_governance() -> None:
    for path in ("Cargo.lock", "uv.lock", ".github/workflows/ci.yml", "docs/tasks/T-001.md"):
        assert vs.matches_any(path, vs.GLOBAL_PROTECTED), path


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("    # TODO: finish", "marker"),
        ("    raise NotImplementedError", "not-implemented"),
        ("@pytest.mark.skip(reason='later')", "skipped-test"),
        ("    pytest.skip('later')", "skipped-test"),
        ("    todo!()", "rust-stub"),
        ('    unimplemented!("x")', "rust-stub"),
        ("#[ignore]", "rust-ignored-test"),
    ],
)
def test_stub_patterns_are_detected(line: str, kind: str) -> None:
    findings = vs.find_stub_lines("f.py", [(3, line)])
    assert len(findings) == 1
    assert f"f.py:3 {kind}" in findings[0]


def test_clean_lines_produce_no_findings() -> None:
    lines = [(1, "def todo_list() -> list[str]:"), (2, "    return ['tonnage']")]
    assert vs.find_stub_lines("f.py", lines) == []


def test_pass_only_functions_are_detected() -> None:
    source = '''
def real(x: int) -> int:
    return x + 1

def stub_pass() -> None:
    pass

def stub_doc() -> None:
    """Only a docstring."""

class Thing:
    def method(self) -> None:
        ...
'''
    findings = vs.find_pass_only_functions("m.py", source)
    names = sorted(f.rsplit(" ", 1)[1] for f in findings)
    assert names == ["method()", "stub_doc()", "stub_pass()"]


def test_interface_declarations_are_exempt() -> None:
    source = '''
from abc import ABC, abstractmethod
from typing import Protocol, overload

class Reader(Protocol):
    def read(self) -> bytes: ...

class Base(ABC):
    @abstractmethod
    def run(self) -> None:
        """Run."""

@overload
def f(x: int) -> int: ...
@overload
def f(x: str) -> str: ...
def f(x: int | str) -> int | str:
    return x
'''
    assert vs.find_pass_only_functions("m.py", source) == []
