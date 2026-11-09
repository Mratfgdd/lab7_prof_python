import argparse
import sys
from collections.abc import Callable, Sequence

from sqlalchemy import make_url

from student_manager.database import database_url, ensure_schema, make_engine, make_session_factory
from student_manager.dbapi import find_students_by_group
from student_manager.exceptions import StudentManagerError
from student_manager.models import Student
from student_manager.services import StudentService

DemoStudent = tuple[str, str, float, str | None]

DEMO: dict[str, list[DemoStudent]] = {
    "PI-21": [
        ("Олена", "Коваленко", 95.0, "olena.kovalenko@example.com"),
        ("Андрій", "Мельник", 78.0, None),
        ("Дмитро", "Бойко", 85.5, None),
    ],
    "PI-22": [
        ("Ірина", "Бондар", 99.5, "iryna.bondar@example.com"),
        ("Тарас", "Гриценко", 55.0, None),
        ("Марта", "Гнатишин", 91.2, None),
    ],
    "PI-23": [
        ("Софія", "Гнатюк", 88.0, None),
        ("Остап", "Дзюба", 62.5, None),
        ("Катерина", "Шевчук", 71.0, None),
    ],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="student-manager", description="Облік студентів у SQLite")
    parser.add_argument(
        "--db", help="URL бази (типово STUDENT_MANAGER_DATABASE_URL або students.db)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="демонстраційні групи та студенти")
    commands.add_parser("groups", help="групи: кількість студентів і середній бал")
    listing = commands.add_parser("students", help="студенти з фільтрами, сортуванням, сторінками")
    listing.add_argument("--group")
    listing.add_argument("--min-grade", type=float)
    listing.add_argument("--sort", choices=("last_name", "average_grade"), default="last_name")
    listing.add_argument("--desc", action="store_true")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--offset", type=int, default=0)
    commands.add_parser("find", help="пошук за прізвищем").add_argument("last_name")
    commands.add_parser("by-group", help="студенти групи (ORM)").add_argument("group")
    commands.add_parser("dbapi-group", help="студенти групи (DB-API)").add_argument("group")
    adding = commands.add_parser("add", help="додати студента")
    for name in ("first_name", "last_name", "group"):
        adding.add_argument(name)
    adding.add_argument("grade", type=float)
    adding.add_argument("--email")
    grading = commands.add_parser("grade", help="змінити середній бал")
    grading.add_argument("student_id", type=int)
    grading.add_argument("grade", type=float)
    emailing = commands.add_parser("email", help="змінити адресу електронної пошти")
    emailing.add_argument("student_id", type=int)
    emailing.add_argument("email")
    moving = commands.add_parser("transfer", help="масове переведення до іншої групи")
    moving.add_argument("group")
    moving.add_argument("student_ids", type=int, nargs="+")
    commands.add_parser("delete", help="видалити студента").add_argument("student_id", type=int)
    removing = commands.add_parser("delete-group", help="видалити групу")
    removing.add_argument("group")
    removing.add_argument("--force", action="store_true")
    commands.add_parser("best", help="найкращий студент")
    commands.add_parser("average", help="середній бал групи").add_argument("group")
    return parser


def seed_demo(service: StudentService) -> None:
    for group_name, students in DEMO.items():
        service.create_group(group_name)
        for first_name, last_name, grade, email in students:
            service.add_student(first_name, last_name, group_name, grade, email)


def print_students(students: Sequence[Student]) -> None:
    print(f"{'ID':>3} {'Студент':<20} {'Група':<6} {'Бал':>6}  Email")
    for student in students:
        print(
            f"{student.id:>3} {student.full_name:<20} {student.group.name:<6} "
            f"{student.average_grade:>6.2f}  {student.email or '-'}"
        )


Handler = Callable[[argparse.Namespace, StudentService, str], None]


def cmd_seed(_args: argparse.Namespace, service: StudentService, _url: str) -> None:
    if service.groups.count():
        print("Дані вже є - seed пропущено")
        return
    seed_demo(service)
    print(f"Створено груп: {service.groups.count()}, студентів: {service.students.count()}")


def cmd_groups(_args: argparse.Namespace, service: StudentService, _url: str) -> None:
    print(f"{'Група':<6} {'Студентів':>9} {'Середній':>9} {'Макс':>6} {'Мін':>6}")
    for item in service.group_report():
        average = "-" if item.average is None else f"{item.average:.2f}"
        best = "-" if item.best is None else f"{item.best:.2f}"
        worst = "-" if item.worst is None else f"{item.worst:.2f}"
        print(f"{item.name:<6} {item.students:>9} {average:>9} {best:>6} {worst:>6}")


def cmd_students(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    print_students(
        service.list_students(
            group_name=args.group,
            min_grade=args.min_grade,
            order_by=args.sort,
            descending=args.desc,
            limit=args.limit,
            offset=args.offset,
        )
    )


def cmd_find(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    found = service.find_by_last_name(args.last_name)
    if found:
        print_students(found)
    else:
        print(f"Студентів з прізвищем {args.last_name!r} не знайдено")


def cmd_by_group(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    print_students(service.filter_by_group(args.group))


def cmd_dbapi_group(args: argparse.Namespace, _service: StudentService, url: str) -> None:
    database = make_url(url).database
    if database is None:
        raise StudentManagerError("DB-API пошук потребує файлової бази SQLite")
    rows = find_students_by_group(database, args.group)
    if not rows:
        print(f"У групі {args.group!r} студентів не знайдено")
    for row in rows:
        print(f"{row.student_id:>3} {row.last_name} {row.first_name} {row.average_grade:.2f}")


def cmd_add(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    added = service.add_student(args.first_name, args.last_name, args.group, args.grade, args.email)
    print(f"Додано: {added.id} {added.full_name} ({args.group}, {added.average_grade})")


def cmd_grade(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    graded = service.update_grade(args.student_id, args.grade)
    print(f"{graded.full_name}: середній бал {graded.average_grade:.2f}")


def cmd_email(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    updated = service.set_email(args.student_id, args.email)
    print(f"{updated.full_name}: {updated.email}")


def cmd_transfer(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    moved = service.transfer_students(args.student_ids, args.group)
    print(f"Переведено студентів: {moved} -> {args.group}")


def cmd_delete(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    service.delete_student(args.student_id)
    print(f"Студента {args.student_id} видалено")


def cmd_delete_group(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    removed = service.delete_group(args.group, force=args.force)
    print(f"Групу {args.group} видалено, студентів разом з нею: {removed}")


def cmd_best(_args: argparse.Namespace, service: StudentService, _url: str) -> None:
    top = service.get_best_student()
    if top is None:
        print("Студентів ще немає")
        return
    print(f"Найкращий студент: {top.full_name} ({top.group.name}, {top.average_grade})")


def cmd_average(args: argparse.Namespace, service: StudentService, _url: str) -> None:
    average = service.get_group_average_by_name(args.group)
    print(f"Середній бал {args.group}: {average:.2f}")


HANDLERS: dict[str, Handler] = {
    "seed": cmd_seed,
    "groups": cmd_groups,
    "students": cmd_students,
    "find": cmd_find,
    "by-group": cmd_by_group,
    "dbapi-group": cmd_dbapi_group,
    "add": cmd_add,
    "grade": cmd_grade,
    "email": cmd_email,
    "transfer": cmd_transfer,
    "delete": cmd_delete,
    "delete-group": cmd_delete_group,
    "best": cmd_best,
    "average": cmd_average,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    url = args.db or database_url()
    engine = make_engine(url)
    try:
        ensure_schema(engine)
        with make_session_factory(engine)() as session:
            HANDLERS[args.command](args, StudentService(session), url)
    except StudentManagerError as error:
        print(f"Помилка: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
