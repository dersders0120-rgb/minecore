"""Write report tables to an Excel workbook (one sheet per table)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path

Cell = str | int | float | None


def write_workbook(
    path: Path, sheets: Mapping[str, tuple[Sequence[str], Sequence[Sequence[Cell]]]]
) -> None:
    """``sheets``: name -> (headers, rows). Numbers stay numbers; NaN becomes empty."""
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Font  # noqa: PLC0415

    book = Workbook()
    first = True
    for name, (headers, rows) in sheets.items():
        sheet = book.active if first else book.create_sheet()
        assert sheet is not None
        first = False
        sheet.title = name[:31]
        sheet.append(list(headers))
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append([None if isinstance(v, float) and math.isnan(v) else v for v in row])
        for col in sheet.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            sheet.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 40)
        sheet.freeze_panes = "A2"
    book.save(path)
