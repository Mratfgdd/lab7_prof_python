import argparse
import ast
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import textwrap
from collections import Counter
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "report"
PDF_PATH = ROOT / "Звіт_ЛР7_Левандівський.pdf"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
PROMPT = r"PS C:\Github\prof_python\Lab_07>"
SRC = ROOT / "src" / "student_manager"
TESTS = ROOT / "tests"
DATABASE = "students.db"
ER_SCRIPT = (
    "import sys; sys.path.insert(0, 'report'); from er_diagram import er_svg; "
    "from student_manager.models import Base; "
    "print(er_svg(Base.metadata, ['groups', 'students']))"
)


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def plural(number: int, one: str, few: str, many: str) -> str:
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def code(source: str, keep: bool | None = None) -> str:
    text = source.strip("\n")
    if keep is None:
        keep = text.count("\n") < 18
    return f'<pre class="code{" keep" if keep else ""}">{esc(text)}</pre>'


def find_node(tree: ast.Module, name: str) -> ast.FunctionDef | ast.ClassDef:
    scope: list[ast.stmt] = tree.body
    node: ast.stmt | None = None
    for piece in name.split("."):
        node = next(n for n in scope if getattr(n, "name", None) == piece)
        scope = getattr(node, "body", [])
    assert isinstance(node, ast.FunctionDef | ast.ClassDef)
    return node


def extract(path: Path, *names: str) -> str:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    parts = []
    for name in names:
        node = find_node(tree, name)
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        parts.append(textwrap.dedent("\n".join(lines[start - 1 : node.end_lineno])))
    return "\n\n".join(parts)


def members(path: Path, class_name: str, *methods: str) -> str:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    node = find_node(ast.parse(source), class_name)
    header = [lines[node.lineno - 1]]
    header += [lines[s.lineno - 1] for s in node.body if isinstance(s, ast.AnnAssign | ast.Assign)]
    parts = ["\n".join(header)]
    parts += [textwrap.indent(extract(path, f"{class_name}.{m}"), "    ") for m in methods]
    return "\n\n".join(parts)


def class_excerpt(path: Path, class_name: str, *keep_methods: str) -> str:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    node = find_node(ast.parse(source), class_name)
    dropped: set[int] = set()
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name not in keep_methods:
            start = min([d.lineno for d in item.decorator_list] + [item.lineno])
            dropped.update(range(start, (item.end_lineno or start) + 1))
    chosen: list[str] = []
    for number in range(node.lineno, (node.end_lineno or node.lineno) + 1):
        if number in dropped:
            continue
        line = lines[number - 1]
        if line.strip() or (chosen and chosen[-1].strip()):
            chosen.append(line)
    while chosen and not chosen[-1].strip():
        chosen.pop()
    return textwrap.dedent("\n".join(chosen))


def constant(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
            return "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
    raise KeyError(name)


def terminal(*steps: tuple[str, str], split: bool = False) -> str:
    body = "\n".join(
        (
            f'<span class="ps">{esc(PROMPT)}</span> {esc(command)}\n{esc(output.rstrip())}'
            if output.strip()
            else f'<span class="ps">{esc(PROMPT)}</span> {esc(command)}'
        )
        for command, output in steps
    )
    return f'<div class="term{" split" if split else ""}">{body}</div>'


def table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def excerpt(output: str, head: int, tail: int) -> tuple[str, int, int]:
    lines = output.rstrip().splitlines()
    middle = lines[head:-tail]
    assert middle
    shown = [*lines[:head], f"... ще рядків: {len(middle)} ...", *lines[-tail:]]
    return "\n".join(shown), len(lines), len(middle)


class Numbering:
    def __init__(self) -> None:
        self.table = 0
        self.figure = 0

    def tcap(self, text: str) -> str:
        self.table += 1
        return f'<p class="tcap">Таблиця {self.table} – {text}</p>'

    def fcap(self, text: str) -> str:
        self.figure += 1
        return f'<p class="fcap">Рисунок {self.figure} – {text}</p>'


class Shell:
    def __init__(self, venv: Path) -> None:
        self.scripts = venv / "Scripts"
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        self.env.pop("STUDENT_MANAGER_DATABASE_URL", None)

    def run(self, *args: str, expect: int = 0) -> str:
        program = args[0]
        executable = self.scripts / ("python.exe" if program == "python" else f"{program}.exe")
        done = subprocess.run(
            [str(executable), *args[1:]],
            cwd=ROOT,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert done.returncode == expect, (args, done.returncode, done.stdout)
        return done.stdout

    def step(self, display: str, *args: str, expect: int = 0) -> tuple[str, str]:
        return display, self.run(*args, expect=expect)


def edge(*args: str) -> None:
    subprocess.run(
        [str(EDGE), "--headless=new", "--disable-gpu", *args],
        check=True,
        capture_output=True,
        timeout=120,
    )


CSS = """
@page { size: A4; margin: 20mm 15mm 20mm 30mm;
  @bottom-center { content: counter(page); font: 12pt "Times New Roman", serif; } }
@page :first { @bottom-center { content: none; } }
html { background: #fff; }
body { margin: 0; color: #000; background: #fff; font-family: "Times New Roman", serif;
  font-size: 14pt; line-height: 1.22; }
p { margin: 0 0 2pt; text-indent: 1.25cm; text-align: justify; }
h1 { font-size: 14pt; text-align: center; margin: 8pt 0 3pt; break-after: avoid; }
code { font-family: Consolas, "Courier New", monospace; font-size: 0.8em; }
pre.code { font-family: Consolas, "Courier New", monospace; font-size: 7.4pt;
  line-height: 1.1; margin: 2pt 0 4pt; padding: 3pt 8pt; white-space: pre-wrap;
  background: #f6f6f4; border-left: 3px solid #c3c2b7; orphans: 4; widows: 4; }
pre.keep { break-inside: avoid; }
.term { font-family: Consolas, "Courier New", monospace; font-size: 6.9pt; line-height: 1.12;
  white-space: pre-wrap; background: #0c0c0c; color: #d4d4d4; padding: 4pt 8pt;
  border-radius: 4px; margin: 2pt 0 3pt; break-inside: avoid; }
.term .ps { color: #f9f1a5; }
.term.split { break-inside: auto; orphans: 8; widows: 8; }
table { width: 100%; border-collapse: collapse; font-size: 10pt; line-height: 1.12;
  margin: 2pt 0 4pt; }
th, td { border: 1px solid #000; padding: 1.5pt 4pt; vertical-align: top; text-align: left; }
th { background: #f0efec; }
tr { break-inside: avoid; }
.tcap { text-indent: 0; text-align: left; margin: 4pt 0 2pt; break-after: avoid; }
.fcap { text-indent: 0; text-align: center; margin: 1pt 0 4pt; }
.figure { break-inside: avoid; margin-top: 3pt; }
.title { height: 255mm; display: flex; flex-direction: column; justify-content: space-between;
  text-align: center; break-after: page; line-height: 1.5; }
.title .main { margin-top: 30mm; }
.title .main .big { font-size: 20pt; font-weight: bold; letter-spacing: 2pt; }
.title .main .theme { font-size: 16pt; font-weight: bold; margin-top: 10pt; }
.title .sign { align-self: flex-end; text-align: left; width: 85mm; margin-top: 30mm; }
.title .city { margin-top: auto; }
"""


def build_html(venv: Path) -> str:
    n = Numbering()
    sh = Shell(venv)
    exp = json.loads((ROOT / "results" / "experiments.json").read_text(encoding="utf-8"))
    er_diagram = sh.run("python", "-c", ER_SCRIPT).strip()
    assert er_diagram.startswith("<svg")
    assert er_diagram.endswith("</svg>")

    for leftover in ROOT.glob(f"{DATABASE}*"):
        leftover.unlink()
    demo = [
        sh.step("alembic upgrade head", "alembic", "upgrade", "head"),
        sh.step("student-manager seed", "student-manager", "seed"),
        sh.step("student-manager groups", "student-manager", "groups"),
        sh.step(
            "student-manager students --sort average_grade --desc --limit 4",
            "student-manager",
            "students",
            "--sort",
            "average_grade",
            "--desc",
            "--limit",
            "4",
        ),
        sh.step("student-manager find Бондар", "student-manager", "find", "Бондар"),
        sh.step("student-manager dbapi-group PI-22", "student-manager", "dbapi-group", "PI-22"),
        sh.step(
            "student-manager add Назар Левандівський PI-21 88.5 --email nazar@example.com",
            "student-manager",
            "add",
            "Назар",
            "Левандівський",
            "PI-21",
            "88.5",
            "--email",
            "nazar@example.com",
        ),
        sh.step("student-manager grade 2 150", "student-manager", "grade", "2", "150", expect=1),
        sh.step(
            "student-manager transfer PI-23 1 2 999",
            "student-manager",
            "transfer",
            "PI-23",
            "1",
            "2",
            "999",
            expect=1,
        ),
        sh.step(
            "student-manager transfer PI-23 1 2",
            "student-manager",
            "transfer",
            "PI-23",
            "1",
            "2",
        ),
        sh.step("student-manager groups", "student-manager", "groups"),
        sh.step(
            "student-manager delete-group PI-23",
            "student-manager",
            "delete-group",
            "PI-23",
            expect=1,
        ),
    ]
    out = [output for _, output in demo]
    assert "Створено груп: 3, студентів: 9" in out[1]
    assert "Бондар Ірина" in out[4]
    assert "Бондар Ірина 99.50" in out[5]
    assert "Додано: 10 Левандівський Назар" in out[6]
    assert "поза діапазоном" in out[7]
    assert "id=999" in out[8]
    assert "Переведено студентів: 2 -> PI-23" in out[9]
    assert "PI-21          2" in out[10]
    assert "PI-23          5" in out[10]
    assert "є студенти: 5" in out[11]

    sql_join = (
        "SELECT s.id, s.last_name, g.name, s.average_grade FROM students s "
        "JOIN groups g ON g.id = s.group_id WHERE g.name = ? ORDER BY s.last_name"
    )
    sql_group = (
        "SELECT g.name, COUNT(s.id) AS students, ROUND(AVG(s.average_grade), 2) AS avg_grade "
        "FROM groups g LEFT JOIN students s ON s.group_id = g.id GROUP BY g.name"
    )
    sql_steps = [
        sh.step(
            f'python tools/sql.py {DATABASE} "{sql_join}" PI-23',
            "python",
            "tools/sql.py",
            DATABASE,
            sql_join,
            "PI-23",
        ),
        sh.step(
            f'python tools/sql.py {DATABASE} "{sql_group}"',
            "python",
            "tools/sql.py",
            DATABASE,
            sql_group,
        ),
    ]
    assert "(5 рядків)" in sql_steps[0][1]
    with closing(sqlite3.connect(ROOT / DATABASE)) as connection:
        ddl = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name <> 'alembic_version' "
                "ORDER BY CASE name WHEN 'groups' THEN 1 WHEN 'students' THEN 2 ELSE 3 END, name"
            )
        ]
    emails = "SELECT last_name, email FROM students WHERE email IS NOT NULL ORDER BY id"
    columns = "SELECT name FROM pragma_table_info('students')"
    migration_steps = [
        sh.step("alembic history", "alembic", "history"),
        sh.step(
            f'python tools/sql.py {DATABASE} "{emails}"', "python", "tools/sql.py", DATABASE, emails
        ),
        sh.step("alembic downgrade -1", "alembic", "downgrade", "-1"),
        sh.step(
            f'python tools/sql.py {DATABASE} "{columns}"',
            "python",
            "tools/sql.py",
            DATABASE,
            columns,
        ),
        sh.step("alembic upgrade head", "alembic", "upgrade", "head"),
        sh.step(
            f'python tools/sql.py {DATABASE} "{emails}"', "python", "tools/sql.py", DATABASE, emails
        ),
        sh.step("alembic current", "alembic", "current"),
    ]
    assert "(3 рядки)" in migration_steps[1][1]
    assert "email" not in migration_steps[3][1]
    assert "(0 рядків)" in migration_steps[5][1]
    assert "0002 (head)" in migration_steps[6][1]
    for leftover in ROOT.glob(f"{DATABASE}*"):
        leftover.unlink()

    collected = sh.run("pytest", "-p", "no:cacheprovider", "--collect-only", "-q")
    per_file = Counter(line.split("::")[0] for line in collected.splitlines() if "::" in line)
    total_tests = sum(per_file.values())
    pytest_v = sh.run("pytest", "-p", "no:cacheprovider", "-v")
    passed = re.search(r"(\d+) passed in ([\d.]+)s", pytest_v)
    assert passed
    assert int(passed.group(1)) == total_tests
    assert not re.search(r"\d+ (failed|error)", pytest_v)
    assert all("PASSED" in line for line in pytest_v.splitlines() if "::" in line)
    shown_v, v_lines, v_hidden = excerpt(pytest_v, 11, 7)
    coverage = sh.run(
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        "--cov=student_manager",
        "--cov-branch",
        "--cov-report=term-missing",
    )
    total_cov = re.search(r"Total coverage: ([\d.]+)%", coverage)
    assert total_cov
    quality = [sh.step("mypy", "mypy"), sh.step("ruff check .", "ruff", "check", ".")]
    assert "Success: no issues found" in quality[0][1]
    assert "All checks passed!" in quality[1][1]
    for name in (".coverage", ".mypy_cache", ".ruff_cache", ".pytest_cache", "htmlcov"):
        target = ROOT / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)

    inj = exp["1_sql_injection"]
    fk = exp["2_foreign_keys"]
    at = exp["3_atomicity"]
    du = exp["4_durability"]
    npo = exp["5_n_plus_one"]
    mg = exp["6_migration_with_data"]
    fm = exp["7_failed_migration"]
    cc = exp["8_commit_cost"]
    ix = exp["9_index_and_aggregate"]
    assert inj["unsafe_rows"] == inj["total_students"] > inj["safe_rows"] == 0
    assert fk["orphans_after_insert_without_pragma"] == 1
    assert "FOREIGN KEY constraint failed" in fk["insert_with_pragma"]
    assert fk["students_left_without_pragma"] == 9
    assert fk["orphans_without_pragma"] == 3
    assert fk["students_left_with_pragma"] == 6
    assert at["naive_commit_per_student"] == 3
    assert at["dbapi_commit_rollback"] == at["transfer_students"] == 0
    assert du["commit"]["grade_after_restart"] == du["new"]
    assert du["flush"]["grade_after_restart"] == du["start"]
    assert npo["lazy"]["queries"] == npo["groups"] + 1
    assert npo["selectinload"]["queries"] == 2
    assert npo["group_by"]["queries"] == 1
    assert (
        npo["lazy"]["students_counted"]
        == npo["selectinload"]["students_counted"]
        == npo["group_by"]["students_counted"]
        == npo["students"]
    )
    assert mg["rows_before"] == mg["rows_after"] == mg["null_emails"]
    assert "UNIQUE constraint failed: students.email" in mg["duplicate_email"]
    assert fm["project_env"]["version_after"] == fm["plain_env"]["version_after"] == "0002"
    assert not fm["project_env"]["note_column_left"]
    assert fm["plain_env"]["note_column_left"]
    assert cc["ratio"] > 2
    assert "INDEX" in ix["with_index"]["plan"]
    assert ix["without_index"]["plan"].startswith("SCAN")
    index_ratio = round(ix["without_index"]["seconds"] / ix["with_index"]["seconds"])
    assert index_ratio > 10
    assert ix["average"]["sql_value"] == ix["average"]["python_value"]
    average_ratio = round(ix["average"]["python_seconds"] / ix["average"]["sql_seconds"])
    assert average_ratio > 5
    rows_text = f"{ix['rows']:,}".replace(",", " ")

    parts: list[str] = []
    add = parts.append
    add(
        """
<section class="title">
  <div>МІНІСТЕРСТВО ОСВІТИ І НАУКИ УКРАЇНИ<br>
  Львівський національний університет імені Івана Франка</div>
  <div class="main">
    <div class="big">ЗВІТ</div>
    <div>до лабораторної роботи №7</div>
    <div>з дисципліни «Професійний Python»</div>
    <div class="theme">Тема: «Робота з базами даних у Python»</div>
  </div>
  <div class="sign">Виконав:<br>студент Левандівський Назар<br><br>
  Перевірив:<br>Куньо Іван Михайлович</div>
  <div class="city">Львів – 2026</div>
</section>"""
    )

    add("<h1>Мета роботи</h1>")
    add(
        "<p>Ознайомитися з організацією шару зберігання даних у Python-застосунках: реляційна "
        "СУБД SQLite, мова SQL, стандарт DB-API, параметризовані запити, CRUD, транзакції та "
        "властивості ACID, Repository Pattern, SQLAlchemy ORM і міграції Alembic. Інтегрувати "
        "persistence layer у проєкт студентів і перевірити його integration tests на окремій "
        "тестовій базі.</p>"
    )

    add("<h1>1. Постановка задачі та структура проєкту</h1>")
    add(
        "<p><b>Варіант 1. Система обліку студентів.</b> Предметну область ЛР1 збережено: студент "
        "має ім'я, прізвище, групу та середній бал (0–100). Потрібно: пов'язані таблиці "
        "<code>groups</code> і <code>students</code> (one-to-many), <code>StudentRepository</code> "
        "і <code>GroupRepository</code>; функції ЛР1 (додавання й виведення, пошук за прізвищем "
        "і групою, найкращий студент, середній бал групи) плюс зміна бала, переведення між "
        "групами та видалення; параметризований пошук за групою через DB-API; транзакція масового "
        "переведення з commit/rollback; міграція, що додає <code>email</code>; integration tests. "
        "Архітектура: CLI → <code>StudentService</code> → репозиторії → SQLAlchemy ORM → SQLite; "
        "<code>dbapi.py</code> працює з SQLite напряму через <code>sqlite3</code>.</p>"
    )
    modules = len(per_file)
    add(
        code(
            f"""Lab_07/
├── pyproject.toml        залежності: sqlalchemy, alembic; [test]/[dev]: pytest, pytest-cov, mypy, ruff
├── alembic.ini           конфігурація Alembic
├── migrations/           env.py; versions/0001_create_groups_and_students.py, 0002_add_student_email.py
├── src/student_manager/
│   ├── database.py       engine, PRAGMA foreign_keys = ON, фабрика сесій
│   ├── models.py         ORM-моделі Group і Student, перевірка полів (validates)
│   ├── repositories.py   Generic Repository; GroupRepository, StudentRepository
│   ├── services.py       StudentService, транзакція transfer_students
│   ├── dbapi.py          пошук студентів групи через sqlite3 (DB-API)
│   ├── validation.py     правила для імені, групи, балу, email; exceptions.py
│   └── main.py           CLI student-manager
├── tests/                conftest.py + {modules} {plural(modules, "модуль", "модулі", "модулів")} integration tests ({total_tests} {plural(total_tests, "тест", "тести", "тестів")})
├── experiments/          run_experiments.py -> results/experiments.json
└── tools/sql.py          SQL-консоль для SQLite (утиліти sqlite3 у Windows немає)"""
        )
    )

    add("<h1>2. Схема бази даних</h1>")
    add(
        f'<div class="figure">{er_diagram}'
        + n.fcap(
            "ER-діаграма (побудована з метаданих ORM-моделей); зовнішній ключ із ON DELETE CASCADE"
        )
        + "</div>"
    )
    add(n.tcap("Таблиці, ключі та поля"))
    add(
        table(
            ["Таблиця", "Ключі", "Поля"],
            [
                [
                    "<b>groups</b> – групи",
                    "PK id; UNIQUE name",
                    "name – код групи, наприклад PI-21 (CHECK: не порожнє)",
                ],
                [
                    "<b>students</b> – студенти",
                    "PK id; FK group_id → groups.id; UNIQUE email; індекси group_id, last_name",
                    "first_name, last_name – ім'я та прізвище (CHECK: не порожні); "
                    "average_grade – середній бал (CHECK: 0…100); group_id – група; "
                    "email – адреса, може бути NULL (додано міграцією 0002, CHECK: формат)",
                ],
            ],
        )
    )
    add(
        "<p>Таблиці створює міграція Alembic. DDL, яку SQLite зберігає в "
        "<code>sqlite_master</code> після <code>alembic upgrade head</code> (students "
        "перебудовано міграцією 0002, тому її назва в лапках):</p>"
    )
    add(
        code(
            "\n".join(f"{statement.strip().replace(chr(9), '  ')};" for statement in ddl),
            keep=False,
        )
    )

    add("<h1>3. Підключення до БД (database.py) і ORM-моделі</h1>")
    add(
        "<p>SQLite за замовчуванням не перевіряє FOREIGN KEY, тому прагма вмикається для "
        "кожного нового з'єднання через подію <code>connect</code> (експеримент 2). URL береться "
        "зі змінної <code>STUDENT_MANAGER_DATABASE_URL</code> або <code>sqlite:///students.db</code>. "
        "Сесія створюється з <code>autoflush=False</code> (SQL надсилається явно) і "
        "<code>expire_on_commit=False</code> (об'єкти лишаються читабельними після commit).</p>"
    )
    add(
        code(
            extract(
                SRC / "database.py", "make_engine", "_enable_foreign_keys", "make_session_factory"
            ),
            keep=False,
        )
    )
    add(
        "<p>Моделі написано в стилі SQLAlchemy 2 (<code>Mapped</code>, <code>mapped_column</code>). "
        "<code>relationship()</code> з <code>back_populates</code> задає one-to-many; "
        '<code>cascade</code> і <code>ondelete="CASCADE"</code> – каскадне видалення. Значення '
        "перевіряються двічі: у <code>@validates</code> (до звернення до БД) і CHECK-обмеженнями "
        "(навіть якщо запис іде повз ORM).</p>"
    )
    add(
        code(
            class_excerpt(SRC / "models.py", "Group")
            + "\n\n\n"
            + class_excerpt(SRC / "models.py", "Student", "_validate_grade"),
            keep=False,
        )
    )

    add("<h1>4. Repository Pattern</h1>")
    add(
        "<p>Generic Repository містить CRUD, спільний для всіх моделей; конкретні репозиторії "
        "додають запити предметної області: пошук за прізвищем, фільтрацію за групою (з JOIN), "
        "пошук з фільтрами, сортуванням і пагінацією, <code>AVG</code>, <code>COUNT</code>, "
        "<code>MIN</code>/<code>MAX</code> з <code>GROUP BY</code>. Репозиторії викликають лише "
        "<code>flush()</code>, а не <code>commit()</code>: межі транзакції визначає сервіс. "
        "Фрагмент:</p>"
    )
    add(
        code(
            members(SRC / "repositories.py", "Repository", "add", "get_by_id")
            + "\n\n\n"
            + members(
                SRC / "repositories.py", "StudentRepository", "find_by_last_name", "update_grade"
            )
            + "\n\n\n"
            + members(SRC / "repositories.py", "GroupRepository", "stats"),
            keep=False,
        )
    )

    add("<h1>5. DB-API і параметризований запит</h1>")
    add(
        "<p>Пошук студентів групи виконано без ORM – через <code>sqlite3</code> (Connection → "
        "Cursor → <code>execute</code> → <code>fetchall</code>). Значення передається окремо від "
        "тексту SQL (плейсхолдер <code>?</code>), тому не може змінити структуру запиту. Рядок "
        f"<code>{esc(inj['payload'])}</code>, вставлений через f-string, перетворює умову на "
        f"<code>... OR '1'='1'</code> і запит повертає {inj['unsafe_rows']} студентів із "
        f"{inj['total_students']}; параметризований запит – {inj['safe_rows']} (експеримент 1).</p>"
    )
    add(
        code(
            constant(SRC / "dbapi.py", "FIND_BY_GROUP")
            + "\n\n\n"
            + extract(SRC / "dbapi.py", "find_students_by_group")
        )
    )

    add("<h1>6. CRUD і робота програми</h1>")
    add(n.tcap("CRUD-операції"))
    add(
        table(
            ["Операція", "Методи StudentService / репозиторіїв", "SQL"],
            [
                [
                    "Create",
                    "<code>create_group</code>, <code>add_student</code>",
                    "INSERT",
                ],
                [
                    "Read",
                    "<code>get_student</code>, <code>find_by_last_name</code>, "
                    "<code>filter_by_group</code>, <code>list_students</code>, "
                    "<code>get_group_average</code>, <code>get_best_student</code>, "
                    "<code>group_report</code>",
                    "SELECT, JOIN, AVG, COUNT, MIN, MAX, GROUP BY",
                ],
                [
                    "Update",
                    "<code>update_grade</code>, <code>set_email</code>, "
                    "<code>transfer_students</code>",
                    "UPDATE",
                ],
                [
                    "Delete",
                    "<code>delete_student</code>, <code>delete_group</code> (непорожню групу – "
                    "лише з <code>force</code>, студенти видаляються каскадно)",
                    "DELETE",
                ],
            ],
        )
    )
    add(
        "<p>У ORM Update – це зміна атрибутів завантаженого об'єкта: сесія відстежує зміни "
        "(Unit of Work) і формує UPDATE під час flush. Сеанс роботи з CLI на новій базі "
        "(помилкові команди повертають код 1 і не змінюють даних):</p>"
    )
    add(terminal(*demo, split=True))
    add(
        n.fcap(
            "Робота програми: міграція, наповнення, пошук, DB-API, додавання, невдалі оновлення "
            "й переведення, успішне переведення, спроба видалити непорожню групу"
        )
    )
    add("<p>Вміст бази, переглянутий SQL-запитами:</p>")
    add(terminal(*sql_steps))
    add(n.fcap("SQL-запити до students.db: JOIN із параметром і GROUP BY з COUNT та AVG"))

    add("<h1>7. Транзакція transfer_students, commit і rollback, ACID</h1>")
    add(
        "<p><code>transfer_students</code> переводить список студентів до іншої групи як єдине "
        "ціле. Після кожного студента виконується <code>flush()</code>, тож UPDATE справді "
        "надсилається в БД; якщо наступного студента не знайдено (або виникла помилка БД), "
        "<code>rollback()</code> скасовує вже надіслані зміни. Інші операції сервісу виконуються "
        "всередині <code>_transaction()</code> – контекстного менеджера з тією самою логікою.</p>"
    )
    add(code(extract(SRC / "services.py", "transfer_students", "StudentService._transaction")))
    add(n.tcap("Як реалізовано властивості ACID"))
    add(
        table(
            ["Властивість", "Реалізація в проєкті", "Перевірка"],
            [
                [
                    "Atomicity",
                    "одна транзакція на операцію; rollback при будь-якому винятку",
                    f"експ. 3: у списку {at['ids']} немає студента {at['missing']}; commit після "
                    f"кожного кроку лишив {at['naive_commit_per_student']} із "
                    f"{len(at['existing'])} студентів у новій групі, DB-API з rollback і "
                    f"transfer_students – {at['transfer_students']}",
                ],
                [
                    "Consistency",
                    "CHECK, UNIQUE, FOREIGN KEY + PRAGMA; правила в @validates і сервісі",
                    f"тести test_constraints; експ. 2: без прагми лишилось "
                    f"{fk['orphans_without_pragma']} «осиротілих» студентів після видалення групи, "
                    "з прагмою – каскадне видалення",
                ],
                [
                    "Isolation",
                    "незафіксовані зміни не видно іншому з'єднанню",
                    "тест test_uncommitted_transfer_is_invisible_to_another_connection: після "
                    "flush інше з'єднання ще бачить стару групу",
                ],
                [
                    "Durability",
                    "після commit SQLite синхронізує журнал і файл БД на диск",
                    f"експ. 4: аварійне завершення процесу після commit – бал "
                    f"{du['commit']['grade_after_restart']}, після flush без commit – "
                    f"{du['flush']['grade_after_restart']}",
                ],
            ],
        )
    )

    add("<h1>8. Міграції Alembic</h1>")
    add(
        "<p>Після <code>alembic init migrations</code> ревізії створено командами "
        '<code>alembic revision --autogenerate --rev-id 0001 -m "create groups and students"</code> '
        'і <code>... --rev-id 0002 -m "add student email"</code> (після додавання поля '
        "<code>email</code> до моделі). Autogenerate додав колонку й UNIQUE, але не порівнює "
        "CHECK-обмеження, тому формат email дописано вручну. На SQLite зміна таблиці "
        "виконується через batch («створити нову таблицю → скопіювати дані → замінити»):</p>"
    )
    add(
        code(
            extract(
                ROOT / "migrations" / "versions" / "0002_add_student_email.py",
                "upgrade",
                "downgrade",
            )
        )
    )
    add(terminal(*migration_steps, split=True))
    add(
        n.fcap(
            "Історія, адреси до та після downgrade, колонки без email, повторний upgrade, "
            "поточна ревізія"
        )
    )
    migration_figure = n.figure
    add(
        f"<p>Результати перевірок. (1) Міграція зберігає дані: після <code>upgrade</code> усі "
        f"{mg['rows_after']} рядків на місці, <code>email</code> дорівнює NULL; UNIQUE дозволяє "
        f"багато NULL, але відхиляє дублікат (експеримент 6). (2) Downgrade видаляє колонку "
        f"разом із даними: після повторного upgrade адрес немає (рисунок {migration_figure}). "
        "Збіг схеми з моделями перевіряє тест <code>test_upgrade_head_creates_schema_identical_"
        "to_models</code> (те саме, що <code>alembic check</code>). (3) Batch-ревізію не можна "
        "відобразити як SQL у режимі <code>--sql</code>: потрібне живе з'єднання для reflection. "
        "(4) У <code>env.py</code> увімкнено <code>transactional_ddl</code> з явним BEGIN: "
        f"штучно зламана ревізія 0003 лишила БД на {fm['project_env']['version_after']} без "
        "нової колонки, а зі стандартним <code>env.py</code> номер версії теж лишився "
        f"{fm['plain_env']['version_after']}, але колонка <code>note</code> залишилась у "
        "таблиці – схема розійшлась із записаною версією (експеримент 7).</p>"
    )

    add("<h1>9. Integration tests</h1>")
    add(
        "<p>Тести перевіряють взаємодію Repository/Service + SQLAlchemy + SQLite на окремій "
        "тестовій БД: кожен тест отримує власний файл <code>test_students.db</code> у "
        "<code>tmp_path</code> (схема – <code>create_all</code>), тож тести незалежні й не "
        "торкаються <code>students.db</code>. Фікстура <code>query</code> читає дані окремим "
        "DB-API з'єднанням – лише те, що справді зафіксовано в БД, а не стан об'єкта в сесії. "
        "Міграції тестуються через <code>alembic.command</code> на такій самій окремій БД.</p>"
    )
    add(
        code(
            extract(TESTS / "conftest.py", "engine")
            + "\n\n\n"
            + extract(
                TESTS / "test_transactions.py",
                "test_database_failure_in_the_middle_rolls_back_updates_already_sent",
            ),
            keep=False,
        )
    )
    add(n.tcap("Файли тестів"))
    describe = {
        "test_validation.py": "правила для імені, групи, балу, email",
        "test_models.py": "ORM-моделі, relationship, каскад, orphan removal",
        "test_constraints.py": "CHECK, UNIQUE, FOREIGN KEY, PRAGMA на рівні БД",
        "test_group_repository.py": "GroupRepository, агрегація по групах",
        "test_student_repository.py": "CRUD Student, пошук, filter_by_group, сортування, пагінація",
        "test_service.py": "бізнес-логіка, середній бал групи, найкращий студент, видалення",
        "test_transactions.py": "transfer_students, commit, rollback, ізоляція",
        "test_dbapi.py": "параметризований пошук через sqlite3, SQL injection",
        "test_migrations.py": "upgrade, downgrade, збереження даних, збіг схеми з моделями",
        "test_cli.py": "команди CLI, коди виходу, повідомлення про помилки",
    }
    assert set(describe) == {Path(name).name for name in per_file}, per_file
    add(
        table(
            ["Файл", "Тестів", "Що перевіряє"],
            [
                [f"<code>{name}</code>", str(per_file[f"tests/{name}"]), what]
                for name, what in describe.items()
            ],
        )
    )
    add(
        terminal(
            ("pytest -v", shown_v),
            ("pytest -q --cov=student_manager --cov-branch --cov-report=term-missing", coverage),
            *quality,
            split=True,
        )
    )
    word = plural(total_tests, "тест", "тести", "тестів")
    add(
        n.fcap(
            f"Результат pytest -v ({total_tests} {word} пройдено за {passed.group(2)} с; показано "
            f"початок і кінець виводу з {v_lines} рядків, пропущено {v_hidden} рядків PASSED), "
            "покриття, mypy (strict = true у pyproject.toml) і ruff"
        )
    )

    add("<h1>10. Експерименти</h1>")
    add(n.tcap("Результати experiments/run_experiments.py (кожен – на власній тимчасовій БД)"))
    add(
        table(
            ["Експеримент", "Результат"],
            [
                [
                    "1. SQL injection",
                    f"запит з f-string повернув {inj['unsafe_rows']} студентів із "
                    f"{inj['total_students']}, параметризований – {inj['safe_rows']}",
                ],
                [
                    "2. FOREIGN KEY",
                    f"без прагми студента з неіснуючою групою збережено "
                    f"(сиріт: {fk['orphans_after_insert_without_pragma']}), видалення групи не "
                    f"каскадне ({fk['orphans_without_pragma']} сиріт); з прагмою – "
                    f"{esc(fk['insert_with_pragma'])}, після видалення групи лишилось "
                    f"{fk['students_left_with_pragma']} студентів",
                ],
                [
                    "3. Atomicity",
                    f"переведення {at['ids']}: commit на кожному студенті – "
                    f"{at['naive_commit_per_student']} переведено, DB-API з rollback – "
                    f"{at['dbapi_commit_rollback']}, transfer_students – {at['transfer_students']}",
                ],
                [
                    "4. Durability",
                    f"os._exit після commit – {du['commit']['grade_after_restart']}, після flush "
                    f"без commit – {du['flush']['grade_after_restart']} (початковий бал "
                    f"{du['start']})",
                ],
                [
                    "5. N+1 запитів",
                    f"{npo['groups']} груп, {npo['students']} студентів: lazy loading – "
                    f"{npo['lazy']['queries']} запитів, {npo['lazy']['seconds']} с; "
                    f"selectinload – {npo['selectinload']['queries']}, "
                    f"{npo['selectinload']['seconds']} с; GROUP BY – {npo['group_by']['queries']}, "
                    f"{npo['group_by']['seconds']} с",
                ],
                [
                    "6. Міграція з даними",
                    f"{mg['rows_before']} рядків: після upgrade {mg['rows_after']}, email NULL – "
                    f"{mg['null_emails']}; дублікат email відхилено",
                ],
                [
                    "7. Збій міграції",
                    f"«{esc(fm['error'])}»: env.py проєкту – схема без змін; стандартний env.py – "
                    "колонка note залишилась при версії "
                    f"{fm['plain_env']['version_after']}",
                ],
                [
                    "8. Вартість commit",
                    f"{cc['operations']} оновлень: commit кожного – "
                    f"{cc['seconds']['commit_each']} с, одна транзакція – "
                    f"{cc['seconds']['one_transaction']} с (у {cc['ratio']} раза швидше)",
                ],
                [
                    "9. Індекс і агрегація",
                    f"{rows_text} студентів, {ix['lookups']} пошуків за прізвищем: "
                    f"{esc(ix['with_index']['plan'])} – {ix['with_index']['seconds']} с; "
                    f"{esc(ix['without_index']['plan'])} – {ix['without_index']['seconds']} с "
                    f"(у {index_ratio} разів повільніше). Середній бал: AVG у SQL – "
                    f"{ix['average']['sql_seconds']} с, вибірка всіх об'єктів і середнє в Python – "
                    f"{ix['average']['python_seconds']} с (результат однаковий, "
                    f"{ix['average']['sql_value']})",
                ],
            ],
        )
    )
    add(
        "<p>Пакетні зміни варто групувати в одну транзакцію (commit найдорожчий), але проміжні "
        "кроки однієї бізнес-операції фіксувати не можна (експеримент 3); агрегації та фільтри "
        "виконують у SQL, а зв'язані колекції завантажують разом.</p>"
    )

    add("<h1>Висновки</h1>")
    add(
        "<p>Систему обліку студентів (варіант 1) переведено на SQLite: дві пов'язані таблиці з "
        "PRIMARY KEY, FOREIGN KEY, UNIQUE і CHECK, SQLAlchemy ORM, Generic Repository, сервіс із "
        "транзакціями, пошук через DB-API та дві міграції Alembic. "
        f"{total_tests} integration {word} на окремій тестовій БД ({total_cov.group(1)} % покриття "
        "з гілками) перевіряють CRUD, пошук, агрегацію, переведення з rollback, обмеження та "
        "міграції. Експерименти показали: FOREIGN KEY у SQLite вмикають явно, rollback захищає від "
        "часткових операцій, параметризовані запити відсікають SQL injection, а міграції SQLite "
        "потребують транзакційного DDL і перевірки згенерованого коду.</p>"
    )

    body = "\n".join(parts)
    return (
        "<!doctype html><html lang='uk'><head><meta charset='utf-8'>"
        f"<title>Звіт ЛР7</title><style>{CSS}</style></head><body>{body}</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(tempfile.gettempdir()) / "lab7_report_edge_profile",
    )
    args = parser.parse_args()
    html_path = REPORT_DIR / "report.html"
    html_path.write_text(build_html(args.venv), encoding="utf-8")
    if PDF_PATH.exists():
        PDF_PATH.unlink()
    edge(
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--user-data-dir={args.profile}",
        f"--print-to-pdf={PDF_PATH}",
        html_path.as_uri(),
    )
    for cache in ROOT.rglob("__pycache__"):
        if ".venv" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)

    import pymupdf

    with pymupdf.open(PDF_PATH) as document:
        print(f"PDF: {PDF_PATH} — {document.page_count} сторінок")


if __name__ == "__main__":
    main()
