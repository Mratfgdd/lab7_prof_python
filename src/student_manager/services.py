from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from student_manager.exceptions import (
    DuplicateEmailError,
    DuplicateGroupError,
    GroupNotEmptyError,
    GroupNotFoundError,
    StudentNotFoundError,
    StudentValidationError,
)
from student_manager.models import Group, Student
from student_manager.repositories import GroupRepository, GroupStats, StudentRepository
from student_manager.validation import clean_email, clean_group_name


def transfer_students(session: Session, student_ids: Sequence[int], new_group_id: int) -> int:
    unique_ids = list(dict.fromkeys(student_ids))
    try:
        if not unique_ids:
            raise StudentValidationError("список студентів для переведення порожній")
        group = session.get(Group, new_group_id)
        if group is None:
            raise GroupNotFoundError(f"групу з id={new_group_id} не знайдено")
        for student_id in unique_ids:
            student = session.get(Student, student_id)
            if student is None:
                raise StudentNotFoundError(f"студента з id={student_id} не знайдено")
            student.group = group
            session.flush()
        session.commit()
    except Exception:
        session.rollback()
        raise
    return len(unique_ids)


class StudentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.groups = GroupRepository(session)
        self.students = StudentRepository(session)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            yield
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def _group(self, name: str) -> Group:
        group = self.groups.get_by_name(clean_group_name(name))
        if group is None:
            raise GroupNotFoundError(f"групу {name} не знайдено")
        return group

    def _student(self, student_id: int) -> Student:
        student = self.students.get_by_id(student_id)
        if student is None:
            raise StudentNotFoundError(f"студента з id={student_id} не знайдено")
        return student

    def create_group(self, name: str) -> Group:
        with self._transaction():
            try:
                return self.groups.create(name)
            except IntegrityError as error:
                if "groups.name" not in str(error.orig):
                    raise
                raise DuplicateGroupError(f"група {name} уже існує") from error

    def add_student(
        self,
        first_name: str,
        last_name: str,
        group_name: str,
        average_grade: float,
        email: str | None = None,
    ) -> Student:
        with self._transaction():
            student = Student(
                first_name=first_name,
                last_name=last_name,
                average_grade=average_grade,
                email=email,
                group=self._group(group_name),
            )
            try:
                self.students.add(student)
            except IntegrityError as error:
                if "students.email" not in str(error.orig):
                    raise
                raise DuplicateEmailError(f"адреса {student.email} уже використовується") from error
        return student

    def get_student(self, student_id: int) -> Student:
        return self._student(student_id)

    def find_by_last_name(self, last_name: str) -> list[Student]:
        return self.students.find_by_last_name(last_name)

    def filter_by_group(self, group_name: str) -> list[Student]:
        return self.students.filter_by_group(self._group(group_name).name)

    def list_students(
        self,
        *,
        group_name: str | None = None,
        min_grade: float | None = None,
        order_by: Literal["last_name", "average_grade"] = "last_name",
        descending: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Student]:
        group_id = None if group_name is None else self._group(group_name).id
        return self.students.search(
            group_id=group_id,
            min_grade=min_grade,
            order_by=order_by,
            descending=descending,
            limit=limit,
            offset=offset,
        )

    def get_group_average(self, group_id: int) -> float:
        average = self.students.group_average(group_id)
        return 0.0 if average is None else average

    def get_group_average_by_name(self, group_name: str) -> float:
        return self.get_group_average(self._group(group_name).id)

    def get_best_student(self) -> Student | None:
        return self.students.best()

    def group_report(self) -> list[GroupStats]:
        return self.groups.stats()

    def update_grade(self, student_id: int, grade: float) -> Student:
        with self._transaction():
            student = self.students.update_grade(student_id, grade)
            if student is None:
                raise StudentNotFoundError(f"студента з id={student_id} не знайдено")
        return student

    def set_email(self, student_id: int, email: str | None) -> Student:
        cleaned = clean_email(email)
        with self._transaction():
            student = self._student(student_id)
            student.email = cleaned
            try:
                self.session.flush()
            except IntegrityError as error:
                if "students.email" not in str(error.orig):
                    raise
                raise DuplicateEmailError(f"адреса {cleaned} уже використовується") from error
        return student

    def transfer_students(self, student_ids: Sequence[int], group_name: str) -> int:
        return transfer_students(self.session, student_ids, self._group(group_name).id)

    def delete_student(self, student_id: int) -> None:
        with self._transaction():
            if not self.students.delete(student_id):
                raise StudentNotFoundError(f"студента з id={student_id} не знайдено")

    def delete_group(self, name: str, *, force: bool = False) -> int:
        with self._transaction():
            group = self._group(name)
            members = len(group.students)
            if members and not force:
                raise GroupNotEmptyError(f"у групі {name} є студенти: {members}")
            self.session.delete(group)
            self.session.flush()
        return members
