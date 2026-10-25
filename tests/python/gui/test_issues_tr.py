"""Turkish validation wording and overlap grouping on the bundled sample."""

from __future__ import annotations

import re
from pathlib import Path

from minecore.drillholes import DrillholeDatabase, validate
from minecore.drillholes.importer import import_folder
from minecore.gui.app import SAMPLE_DIR
from minecore.gui.issues_tr import describe, rows
from minecore.project import Project

ENGLISH = re.compile(r"\b(is not|overlaps|collar|between|exceeds|share|flagged|below)\b")


def test_sample_report_is_turkish_and_grouped(tmp_path: Path) -> None:
    with Project.create(tmp_path / "t.mcproj", "t") as project:
        db = DrillholeDatabase.create(project, "S")
        import_folder(db, SAMPLE_DIR)
        report = validate(db)
        issues = report.issues
        for issue in issues:
            assert not ENGLISH.search(describe(issue)), describe(issue)
        table = rows(issues)
        overlaps = [i for i in issues if i.code == "INTERVAL_OVERLAP"]
        shown = [r for r in table if r.code == "INTERVAL_OVERLAP"]
        assert len(overlaps) > 100  # the sample's long interval overlaps the whole hole
        assert len(shown) < 20
        cascade = [r for r in shown if "aralıkla çakışıyor" in r.text]
        assert cascade
        long_row = next(r for r in cascade if r.line == "3579")
        count = re.search(r"(\d+) aralıkla", long_row.text)
        assert count is not None
        assert int(count[1]) > 100
        # Every other issue is still listed.
        others = [i for i in issues if i.code != "INTERVAL_OVERLAP"]
        assert len([r for r in table if r.code != "INTERVAL_OVERLAP"]) == len(others)
