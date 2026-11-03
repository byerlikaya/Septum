"""Render a spreadsheet's cell grid as text that keeps every value in its column.

Shared by the XLSX and ODS ingesters. A value's position is its column, so
empty cells stay as empty tab-separated fields instead of being dropped;
otherwise a value after a gap would move into the wrong generated column.
The first non-empty row is the header: its text is replaced by generic,
non-PII column labels, one per column of the sheet's widest row. Columns
empty in every row are trimmed from the left, trailing empty fields are not
written, and empty rows produce no text.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from ...models.spreadsheet_schema import generated_technical_label


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN from pandas
        return True
    return isinstance(value, str) and not value.strip()


def _cell_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _trimmed(row: Sequence[Any]) -> List[Optional[str]]:
    """Row cells as text (``None`` when empty), without trailing empty cells."""
    cells = [None if _is_empty(value) else _cell_text(value) for value in row]
    while cells and cells[-1] is None:
        cells.pop()
    return cells


def render_sheet(
    title: str, rows: Iterable[Sequence[Any]]
) -> Tuple[str, int, Optional[List[str]]]:
    """Return a sheet's text block (empty when it has no values), row count and header labels."""
    row_count = 0
    grid: List[List[Optional[str]]] = []
    for row in rows:
        row_count += 1
        cells = _trimmed(row)
        if cells:
            grid.append(cells)
    if not grid:
        return "", row_count, None

    offset = min(next(i for i, cell in enumerate(cells) if cell is not None) for cells in grid)
    width = max(len(cells) for cells in grid) - offset
    labels = [generated_technical_label(idx) for idx in range(width)]
    lines = ["\t".join(labels)]
    lines.extend("\t".join(cell or "" for cell in cells[offset:]) for cells in grid[1:])
    header = f"# Sheet: {title} — columns: " + " | ".join(labels)
    return header + "\n" + "\n".join(lines), row_count, labels
