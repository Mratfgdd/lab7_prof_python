import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from student_manager.database import make_engine, make_session_factory
from student_manager.dbapi import FIND_BY_GROUP
from student_manager.exceptions import StudentNotFoundError
from student_manager.main import seed_demo
from student_manager.models import Base, Group, Student
from student_manager.services import StudentService, transfer_students

RESULTS = ROOT / "results" / "experiments.json"
PAYLOAD = "PI-21' OR '1'='1"

PLAIN_ENV = """from alembic import context
from sqlalchemy import create_engine, pool

from student_manager.database import database_url
from student_manager.models import Base

config = context.config
connectable = create_engine(
    config.get_main_option("sqlalchemy.url") or database_url(), poolclass=pool.NullPool
)
with connectable.connect() as connection:
    context.configure(
        connection=connection, target_metadata=Base.metadata, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()
"""

BROKEN_REVISION = """from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("students") as batch_op:
        batch_op.add_column(sa.Column("note", sa.String(50)))
    raise RuntimeError("помилка посередині міграції")


def downgrade() -> None:
    pass
"""

CRASH = """
import os, sys
from student_manager.database import make_engine, make_session_factory
from student_manager.services import StudentService

session = make_session_factory(make_engine(sys.argv[1]))()
service = StudentService(session)
if sys.argv[2] == "commit":
    service.update_grade(1, 50.0)
else:
    service.get_student(1).average_grade = 50.0
    session.flush()
os._exit(1)
"""


@contextmanager
def demo_database() -> Iterator[tuple[Engine, Path]]:
    folder = Path(tempfile.mkdtemp(prefix="lab7_"))
    path = folder / "students.db"
    engine = make_engine(f"sqlite:///{path}")
    try:
        Base.metadata.create_all(engine)
        with make_session_factory(engine)() as session:
            seed_demo(StudentService(session))
        yield engine, path
    finally:
        engine.dispose()
        shutil.rmtree(folder, ignore_errors=True)


def scalar(path: Path, sql: str, *params: object) -> Any:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql, params).fetchone()[0]


def best_of(repeats: int, work: Callable[[], object]) -> float:
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        work()
        timings.append(time.perf_counter() - start)
    return min(timings)


def sql_injection() -> dict[str, Any]:
    with demo_database() as (_, path), closing(sqlite3.connect(path)) as connection:
        unsafe_sql = (
            "SELECT s.last_name FROM students s JOIN groups g ON g.id = s.group_id "
            f"WHERE g.name = '{PAYLOAD}'"
        )
        unsafe = connection.execute(unsafe_sql).fetchall()
        safe = connection.execute(FIND_BY_GROUP, (PAYLOAD,)).fetchall()
        total = connection.execute("SELECT count(*) FROM students").fetchone()[0]
    return {
        "payload": PAYLOAD,
        "unsafe_sql": unsafe_sql,
        "total_students": total,
        "unsafe_rows": len(unsafe),
        "safe_rows": len(safe),
    }


def foreign_keys() -> dict[str, Any]:
    insert = (
        "INSERT INTO students (first_name, last_name, average_grade, group_id) "
        "VALUES ('Олена', 'Тест', 90, 999)"
    )
    result: dict[str, Any] = {}
    with demo_database() as (engine, path):
        with closing(sqlite3.connect(path)) as raw:
            raw.execute(insert)
            raw.commit()
            result["orphans_after_insert_without_pragma"] = raw.execute(
                "SELECT count(*) FROM students WHERE group_id = 999"
            ).fetchone()[0]
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(insert)
        except IntegrityError as error:
            result["insert_with_pragma"] = f"IntegrityError: {error.orig}"
    with demo_database() as (_, path):
        with closing(sqlite3.connect(path)) as raw:
            raw.execute("DELETE FROM groups WHERE name = 'PI-23'")
            raw.commit()
            result["students_left_without_pragma"] = raw.execute(
                "SELECT count(*) FROM students"
            ).fetchone()[0]
            result["orphans_without_pragma"] = raw.execute(
                "SELECT count(*) FROM students s LEFT JOIN groups g ON g.id = s.group_id "
                "WHERE g.id IS NULL"
            ).fetchone()[0]
    with demo_database() as (engine, path):
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM groups WHERE name = 'PI-23'")
        result["students_left_with_pragma"] = scalar(path, "SELECT count(*) FROM students")
    return result


def naive_transfer(session: Session, ids: Sequence[int], group_id: int) -> None:
    for student_id in ids:
        student = session.get(Student, student_id)
        if student is None:
            raise StudentNotFoundError(f"студента з id={student_id} не знайдено")
        student.group_id = group_id
        session.commit()


def dbapi_transfer(path: Path, ids: Sequence[int], group_id: int) -> None:
    connection = sqlite3.connect(path)
    try:
        for student_id in ids:
            cursor = connection.execute(
                "UPDATE students SET group_id = ? WHERE id = ?", (group_id, student_id)
            )
            if cursor.rowcount == 0:
                raise StudentNotFoundError(f"студента з id={student_id} не знайдено")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def moved(path: Path, ids: Sequence[int], group_id: int) -> int:
    marks = ",".join("?" * len(ids))
    return int(
        scalar(
            path,
            f"SELECT count(*) FROM students WHERE group_id = ? AND id IN ({marks})",
            group_id,
            *ids,
        )
    )


def atomicity() -> dict[str, Any]:
    ids = [1, 2, 3, 777, 5]
    existing = [1, 2, 3, 5]
    result: dict[str, Any] = {"ids": ids, "existing": existing, "missing": 777}
    runners: dict[str, Callable[[Engine, Path, int], object]] = {
        "naive_commit_per_student": lambda engine, _path, gid: naive_transfer(
            make_session_factory(engine)(), ids, gid
        ),
        "dbapi_commit_rollback": lambda _engine, path, gid: dbapi_transfer(path, ids, gid),
        "transfer_students": lambda engine, _path, gid: transfer_students(
            make_session_factory(engine)(), ids, gid
        ),
    }
    for name, runner in runners.items():
        with demo_database() as (engine, path):
            target = scalar(path, "SELECT id FROM groups WHERE name = 'PI-23'")
            try:
                runner(engine, path, target)
            except StudentNotFoundError as error:
                result["error"] = str(error)
            result[name] = moved(path, existing, target)
    return result


def durability() -> dict[str, Any]:
    result: dict[str, Any] = {"start": 95.0, "new": 50.0}
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for mode in ("commit", "flush"):
        with demo_database() as (engine, path):
            engine.dispose()
            done = subprocess.run(
                [sys.executable, "-c", CRASH, f"sqlite:///{path}", mode],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            result[mode] = {
                "exit_code": done.returncode,
                "grade_after_restart": scalar(
                    path, "SELECT average_grade FROM students WHERE id = 1"
                ),
            }
    return result


def n_plus_one(groups: int = 300, per_group: int = 10) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="lab7_n1_"))
    engine = make_engine(f"sqlite:///{folder / 'students.db'}")
    try:
        Base.metadata.create_all(engine)
        with make_session_factory(engine)() as session:
            for number in range(groups):
                group = Group(name=f"{chr(65 + number // 100) * 2}-{number % 100:02d}")
                session.add(group)
                session.flush()
                session.add_all(
                    Student(
                        first_name="Ім'я",
                        last_name=f"Прізвище{index}",
                        average_grade=50.0 + index,
                        group=group,
                    )
                    for index in range(per_group)
                )
            session.commit()
        queries = [0]

        @event.listens_for(engine, "before_cursor_execute")
        def count(*_args: Any) -> None:
            queries[0] += 1

        def lazy() -> int:
            with make_session_factory(engine)() as session:
                return sum(len(group.students) for group in session.scalars(select(Group)))

        def eager() -> int:
            with make_session_factory(engine)() as session:
                statement = select(Group).options(selectinload(Group.students))
                return sum(len(group.students) for group in session.scalars(statement))

        def aggregate() -> int:
            with make_session_factory(engine)() as session:
                return sum(item.students for item in StudentService(session).group_report())

        result: dict[str, Any] = {"groups": groups, "students": groups * per_group}
        for name, work in (("lazy", lazy), ("selectinload", eager), ("group_by", aggregate)):
            queries[0] = 0
            total = work()
            result[name] = {
                "queries": queries[0],
                "students_counted": total,
                "seconds": round(best_of(3, work), 4),
            }
        return result
    finally:
        engine.dispose()
        shutil.rmtree(folder, ignore_errors=True)


def alembic_config(folder: Path, url: str) -> Config:
    config = Config(str(folder / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.set_main_option("script_location", str(folder / "migrations"))
    config.attributes["configure_logger"] = False
    return config


def migration_with_data(rows: int = 1000) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="lab7_mig_"))
    path = folder / "students.db"
    try:
        config = alembic_config(ROOT, f"sqlite:///{path}")
        command.upgrade(config, "0001")
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("INSERT INTO groups (name) VALUES ('PI-21')")
            connection.executemany(
                "INSERT INTO students (first_name, last_name, average_grade, group_id) "
                "VALUES ('Ім''я', ?, ?, 1)",
                [(f"Прізвище{n}", 60.0 + n % 40) for n in range(rows)],
            )
            connection.commit()
        before = scalar(path, "SELECT count(*) FROM students")
        command.upgrade(config, "head")
        result: dict[str, Any] = {
            "rows_before": before,
            "rows_after": scalar(path, "SELECT count(*) FROM students"),
            "null_emails": scalar(path, "SELECT count(*) FROM students WHERE email IS NULL"),
        }
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE students SET email = 'same@example.com' WHERE id = 1")
            try:
                connection.execute("UPDATE students SET email = 'same@example.com' WHERE id = 2")
            except sqlite3.IntegrityError as error:
                result["duplicate_email"] = f"IntegrityError: {error}"
        return result
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def failed_migration() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, plain in (("project_env", False), ("plain_env", True)):
        folder = Path(tempfile.mkdtemp(prefix="lab7_broken_"))
        try:
            shutil.copy(ROOT / "alembic.ini", folder)
            shutil.copytree(
                ROOT / "migrations",
                folder / "migrations",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            (folder / "migrations" / "versions" / "0003_broken.py").write_text(
                BROKEN_REVISION, encoding="utf-8"
            )
            if plain:
                (folder / "migrations" / "env.py").write_text(PLAIN_ENV, encoding="utf-8")
            path = folder / "students.db"
            config = alembic_config(folder, f"sqlite:///{path}")
            command.upgrade(config, "0002")
            try:
                command.upgrade(config, "head")
            except RuntimeError as error:
                result["error"] = str(error)
            with closing(sqlite3.connect(path)) as connection:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                    )
                ]
                columns = [row[1] for row in connection.execute("PRAGMA table_info(students)")]
                version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[
                    0
                ]
            result[name] = {
                "version_after": version,
                "tables": tables,
                "note_column_left": "note" in columns,
            }
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    return result


def commit_cost(operations: int = 300, repeats: int = 3) -> dict[str, Any]:
    timings: dict[str, list[float]] = {"commit_each": [], "one_transaction": []}
    for _ in range(repeats):
        for mode in timings:
            with demo_database() as (engine, _):
                with make_session_factory(engine)() as session:
                    service = StudentService(session)
                    start = time.perf_counter()
                    if mode == "commit_each":
                        for number in range(operations):
                            service.update_grade(1, 50.0 + number % 50)
                    else:
                        for number in range(operations):
                            service.students.update_grade(1, 50.0 + number % 50)
                        session.commit()
                    timings[mode].append(time.perf_counter() - start)
    medians = {mode: statistics.median(values) for mode, values in timings.items()}
    return {
        "operations": operations,
        "repeats": repeats,
        "seconds": {mode: round(value, 3) for mode, value in medians.items()},
        "ratio": round(medians["commit_each"] / medians["one_transaction"], 1),
    }


def big_database(rows: int) -> tuple[Path, Path]:
    folder = Path(tempfile.mkdtemp(prefix="lab7_big_"))
    path = folder / "students.db"
    engine = make_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    rng = random.Random(2026)
    with closing(sqlite3.connect(path)) as connection:
        connection.executemany(
            "INSERT INTO groups (name) VALUES (?)", [(f"PI-{n:02d}",) for n in range(1, 31)]
        )
        connection.executemany(
            "INSERT INTO students (first_name, last_name, average_grade, group_id) "
            "VALUES ('Ім''я', ?, ?, ?)",
            [
                (f"Прізвище{n % 5000:04d}", round(rng.uniform(40, 100), 2), 1 + n % 30)
                for n in range(rows)
            ],
        )
        connection.commit()
    return folder, path


def index_and_aggregate(rows: int = 100_000) -> dict[str, Any]:
    folder, path = big_database(rows)
    try:
        lookup = "SELECT id FROM students WHERE last_name = ?"
        names = [f"Прізвище{n:04d}" for n in range(0, 5000, 25)]

        def search() -> None:
            with closing(sqlite3.connect(path)) as connection:
                for name in names:
                    connection.execute(lookup, (name,)).fetchall()

        def plan() -> str:
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute("EXPLAIN QUERY PLAN " + lookup, (names[0],)).fetchone()
            return str(row[3])

        result: dict[str, Any] = {"rows": rows, "lookups": len(names)}
        result["with_index"] = {"plan": plan(), "seconds": round(best_of(3, search), 4)}
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("DROP INDEX ix_students_last_name")
            connection.commit()
        result["without_index"] = {"plan": plan(), "seconds": round(best_of(3, search), 4)}
        engine = make_engine(f"sqlite:///{path}")

        def sql_average() -> float:
            with make_session_factory(engine)() as session:
                return float(session.scalar(select(func.avg(Student.average_grade))) or 0.0)

        def python_average() -> float:
            with make_session_factory(engine)() as session:
                grades = [s.average_grade for s in session.scalars(select(Student))]
            return statistics.fmean(grades)

        result["average"] = {
            "sql_value": round(sql_average(), 4),
            "python_value": round(python_average(), 4),
            "sql_seconds": round(best_of(3, sql_average), 4),
            "python_seconds": round(best_of(3, python_average), 4),
        }
        engine.dispose()
        return result
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    os.chdir(ROOT)
    results = {
        "1_sql_injection": sql_injection(),
        "2_foreign_keys": foreign_keys(),
        "3_atomicity": atomicity(),
        "4_durability": durability(),
        "5_n_plus_one": n_plus_one(),
        "6_migration_with_data": migration_with_data(),
        "7_failed_migration": failed_migration(),
        "8_commit_cost": commit_cost(),
        "9_index_and_aggregate": index_and_aggregate(),
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
