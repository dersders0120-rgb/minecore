"""Block model CSV import."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, BlockModelError, Grid
from minecore.blockmodel.importer import import_block_csv, parse_units
from minecore.gui.resource import export_block_model_csv
from minecore.project import Project


def test_round_trip_through_csv_export(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "r.mcproj", "r")
    grid = Grid((1000.0, 2000.0, 300.0), (10.0, 20.0, 5.0), (4, 3, 2))
    model = BlockModel.create(project, "A", grid)
    rng = np.random.default_rng(1)
    au = rng.uniform(0, 3, grid.blocks)
    au[5] = np.nan
    model.set_attributes({"au": au, "lito": np.repeat([1.0, 2.0], 12)}, {}, "data")
    path = tmp_path / "a.csv"
    export_block_model_csv(path, model)
    out = import_block_csv(project, path, "B", units={"au": "g/t"})
    b = out.model
    assert b.grid == grid
    assert np.allclose(b.values("au"), au, equal_nan=True)
    assert b.values("lito").tolist() == model.values("lito").tolist()
    assert b.attributes["au"]["unit"] == "g/t"
    assert out.merged_rows == 0
    # Integer index columns i, j, k are imported as attributes too.
    assert "i" in out.attributes
    project.close()


def test_sub_blocks_merge_by_volume(tmp_path: Path) -> None:
    text = (
        "XC;YC;ZC;XINC;YINC;ZINC;CU;ZONE\n"
        "2,5;5;5;5;10;10;1,0;ox\n"
        "7,5;5;5;5;10;10;3,0;ox\n"
        "15;5;5;10;10;10;2,0;su\n"
        "12,5;15;5;5;10;10;4,0;su\n"
    )
    path = tmp_path / "sub.csv"
    path.write_text(text, encoding="utf-8")
    project = Project.create(tmp_path / "s.mcproj", "s")
    out = import_block_csv(project, path, "S")
    g = out.model.grid
    assert g.size == (10.0, 10.0, 10.0)
    assert g.count == (2, 2, 1)
    assert g.origin == (0.0, 0.0, 0.0)
    cu = out.model.values("CU")
    assert cu[0] == pytest.approx(2.0)  # two half blocks 1 and 3
    assert cu[1] == pytest.approx(2.0)
    assert cu[3] == pytest.approx(4.0)  # a quarter... sub-block alone in its parent
    assert np.isnan(cu[2])
    assert out.merged_rows == 1
    assert out.skipped_columns == ("ZONE",)
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(BlockModelError):
        import_block_csv(project, bad, "X")
    project.close()


def test_parse_units() -> None:
    assert parse_units("au=g/t; cu = %") == {"au": "g/t", "cu": "%"}


def test_cli_import(tmp_path: Path) -> None:
    import io  # noqa: PLC0415

    from minecore.cli import main  # noqa: PLC0415

    path = tmp_path / "m.csv"
    path.write_text("x,y,z,au\n5,5,5,1.5\n15,5,5,2.5\n", encoding="utf-8")
    p = str(tmp_path / "c.mcproj")
    out = io.StringIO()
    assert main(["project", "create", p, "--name", "c"], out) == 0
    assert main(["bm", "import", p, "M", str(path), "--units", "au=g/t"], out) == 0
    assert "2 blocks" in out.getvalue()
