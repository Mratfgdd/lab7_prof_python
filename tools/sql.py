import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing

USAGE = (
    'Використання: python tools/sql.py students.db "SELECT ..." [параметр ...]\n'
    'Значення після запиту підставляються в "?" як параметри DB-API, а не в текст SQL.'
)


def render(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    cells = [[("NULL" if value is None else str(value)) for value in row] for row in rows]
    widths = [max([len(h), *(len(r[i]) for r in cells)]) for i, h in enumerate(headers)]

    def line(values: Sequence[str]) -> str:
        return "│ " + " │ ".join(v.ljust(w) for v, w in zip(values, widths, strict=True)) + " │"

    border = "─┼─".join("─" * w for w in widths)
    return "\n".join([line(headers), f"├─{border}─┤", *(line(r) for r in cells)])


def rows_word(number: int) -> str:
    if number % 10 == 1 and number % 100 != 11:
        return "рядок"
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return "рядки"
    return "рядків"


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        print(USAGE)
        return 2
    database, query, *parameters = argv
    with closing(sqlite3.connect(database)) as connection:
        cursor = connection.execute(query, parameters)
        if cursor.description is None:
            connection.commit()
            print(f"змінено рядків: {cursor.rowcount}")
            return 0
        headers = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
    print(render(headers, rows))
    print(f"({len(rows)} {rows_word(len(rows))})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
