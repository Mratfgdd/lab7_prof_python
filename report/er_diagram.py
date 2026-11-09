import textwrap
from html import escape

from sqlalchemy import CheckConstraint, MetaData, Table, UniqueConstraint
from sqlalchemy.dialects import sqlite

ROW = 16
HEADER = 24
CHECK_LINE = 11.5
WIDTH = 235
GAP = 70
TOP = 6
CHECK_CHARS = 46


def _column_rows(table: Table) -> list[tuple[str, str, str]]:
    unique = {
        c.columns.keys()[0]
        for c in table.constraints
        if isinstance(c, UniqueConstraint) and len(c.columns) == 1
    }
    rows = []
    for column in table.columns:
        marks = [
            mark
            for mark, flag in (
                ("PK", column.primary_key),
                ("FK", bool(column.foreign_keys)),
                ("UQ", column.name in unique),
            )
            if flag
        ]
        rows.append((",".join(marks), column.name, column.type.compile(dialect=sqlite.dialect())))
    return rows


def _check_lines(table: Table) -> list[str]:
    checks = sorted(
        (c for c in table.constraints if isinstance(c, CheckConstraint)), key=lambda c: str(c.name)
    )
    lines: list[str] = []
    for check in checks:
        lines += textwrap.wrap(f"CHECK {check.sqltext}", CHECK_CHARS, subsequent_indent="  ")
    return lines


def er_svg(metadata: MetaData, order: list[str]) -> str:
    boxes: dict[str, tuple[float, float]] = {}
    parts: list[str] = []
    row_y: dict[tuple[str, str], float] = {}
    for index, name in enumerate(order):
        table = metadata.tables[name]
        x = 4 + index * (WIDTH + GAP)
        rows = _column_rows(table)
        checks = _check_lines(table)
        height = HEADER + ROW * len(rows) + 6 + (CHECK_LINE * len(checks) + 6 if checks else 0)
        boxes[name] = (x, height)
        parts.append(
            f'<rect x="{x}" y="{TOP}" width="{WIDTH}" height="{height}" rx="3" '
            'fill="#fff" stroke="#3d3d3a" stroke-width="1.1"/>'
        )
        parts.append(
            f'<rect x="{x}" y="{TOP}" width="{WIDTH}" height="{HEADER}" rx="3" '
            'fill="#eceae4" stroke="#3d3d3a" stroke-width="1.1"/>'
        )
        parts.append(f'<text x="{x + WIDTH / 2}" y="{TOP + 16}" class="nm">{name}</text>')
        for i, (marks, column, kind) in enumerate(rows):
            y = TOP + HEADER + 13 + i * ROW
            row_y[(name, column)] = y - 4
            weight = ' font-weight="bold"' if "PK" in marks else ""
            parts.append(f'<text x="{x + 5}" y="{y}" class="mk">{marks}</text>')
            parts.append(f'<text x="{x + 32}" y="{y}" class="cl"{weight}>{column}</text>')
            parts.append(f'<text x="{x + WIDTH - 5}" y="{y}" class="tp">{escape(kind)}</text>')
        if checks:
            line_y = TOP + HEADER + ROW * len(rows) + 5
            parts.append(
                f'<line x1="{x}" y1="{line_y}" x2="{x + WIDTH}" y2="{line_y}" '
                'stroke="#3d3d3a" stroke-width="0.6" stroke-dasharray="3 2"/>'
            )
            for i, text in enumerate(checks):
                parts.append(
                    f'<text x="{x + 5}" y="{line_y + 11 + i * CHECK_LINE}" '
                    f'class="ck" xml:space="preserve">{escape(text)}</text>'
                )
    for name in order:
        for fk in metadata.tables[name].foreign_keys:
            parent = fk.column.table.name
            x1, x2 = boxes[parent][0] + WIDTH, boxes[name][0]
            y1, y2 = row_y[(parent, fk.column.name)], row_y[(name, fk.parent.name)]
            mid = (x1 + x2) / 2
            parts.append(
                f'<path d="M{x1} {y1} H{mid} V{y2} H{x2}" fill="none" '
                'stroke="#1f5f8b" stroke-width="1.3"/>'
            )
            parts.append(
                f'<path d="M{x1 + 7} {y1 - 6} V{y1 + 6}" stroke="#1f5f8b" stroke-width="1.3"/>'
            )
            parts.append(
                f'<path d="M{x2 - 10} {y2} L{x2} {y2 - 6} M{x2 - 10} {y2} L{x2} '
                f'{y2 + 6} M{x2 - 13} {y2 - 6} V{y2 + 6}" fill="none" '
                'stroke="#1f5f8b" stroke-width="1.3"/>'
            )
            parts.append(f'<text x="{mid}" y="{min(y1, y2) - 6}" class="rl">1 : N</text>')
    width = 8 + len(order) * WIDTH + (len(order) - 1) * GAP
    height = TOP + max(h for _, h in boxes.values()) + 4
    style = (
        "<style>text{font-family:Consolas,'Courier New',monospace;fill:#1a1a18}"
        ".nm{font:bold 13px 'Times New Roman',serif;text-anchor:middle}"
        ".mk{font-size:9px;fill:#1f5f8b;font-weight:bold}.cl{font-size:10.5px}"
        ".tp{font-size:9.5px;fill:#5f5e5a;text-anchor:end}.ck{font-size:8.6px;fill:#5f5e5a}"
        ".rl{font:italic 10.5px 'Times New Roman',serif;fill:#1f5f8b;text-anchor:middle}"
        "</style>"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="ER-діаграма">{style}{"".join(parts)}</svg>'
    )
