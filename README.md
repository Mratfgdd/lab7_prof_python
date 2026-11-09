# Student Manager: persistence layer

Лабораторна робота №7 з дисципліни «Професійний Python»: «Робота з базами даних у Python».
Варіант 1 — «Система обліку студентів».

Студент (ім'я, прізвище, група, середній бал 0–100) зберігається в SQLite. Дві пов'язані таблиці
`groups` і `students` (one-to-many), SQLAlchemy ORM, Repository Pattern, транзакції з
commit/rollback, пошук через DB-API, міграції Alembic та integration tests.

## Встановлення

```
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

Без встановлення пакета: `python -m pip install -r requirements.txt`, далі `pytest` з кореня
проєкту (`pythonpath = ["src"]` у `pyproject.toml`).

## База даних

```
alembic upgrade head          # створити або оновити схему в students.db
alembic current               # поточна ревізія
alembic downgrade -1          # відкотити останню міграцію
alembic check                 # моделі й міграції узгоджені
```

Адреса бази береться зі змінної `STUDENT_MANAGER_DATABASE_URL` (типово `sqlite:///students.db`).

Міграції: `0001` — таблиці groups і students; `0002` — поле `students.email` (UNIQUE, може бути NULL,
CHECK на формат).

## Запуск програми

```
student-manager seed                              # демонстраційні групи та студенти
student-manager groups                            # кількість студентів, AVG, MAX, MIN по групах
student-manager students --group PI-22 --sort average_grade --desc --limit 5
student-manager find Бондар                       # пошук за прізвищем
student-manager by-group PI-21                    # студенти групи (ORM)
student-manager dbapi-group PI-21                 # студенти групи (DB-API, параметризований запит)
student-manager add Назар Левандівський PI-21 88.5 --email nazar@example.com
student-manager grade 2 91.5                      # змінити середній бал
student-manager transfer PI-23 1 2 3              # масове переведення (одна транзакція)
student-manager delete 3
student-manager delete-group PI-23 --force
student-manager best
student-manager average PI-21
python tools/sql.py students.db "SELECT * FROM students WHERE last_name = ?" Бондар
```

## Тести та перевірки

```
pytest -v                                         # кожен тест працює на власній БД у tmp_path
pytest --cov=student_manager --cov-branch --cov-report=term-missing
mypy
ruff check .
python experiments/run_experiments.py            # 9 експериментів -> results/experiments.json
```

## Структура

```
src/student_manager/
    database.py       engine, PRAGMA foreign_keys = ON, фабрика сесій
    models.py         ORM-моделі Group і Student
    repositories.py   Generic Repository, GroupRepository, StudentRepository
    services.py       StudentService, transfer_students (commit/rollback)
    dbapi.py          пошук студентів групи через sqlite3
    validation.py     правила для імені, групи, балу, email
    exceptions.py     власні винятки
    main.py           CLI
migrations/           Alembic: env.py, versions/0001, versions/0002
tests/                conftest.py та integration tests
experiments/          експерименти для звіту
report/               генератор PDF-звіту
tools/sql.py          SQL-консоль для SQLite
```
