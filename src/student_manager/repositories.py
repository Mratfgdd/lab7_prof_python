from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from sqlalchemy import Select, func, inspect, select
from sqlalchemy.orm import Session

from student_manager.models import Base, Group, Student

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get_by_id(self, entity_id: int) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def get_all(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        statement = (
            select(self.model)
            .order_by(*inspect(self.model).primary_key)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(self.model)) or 0

    def delete(self, entity_id: int) -> bool:
        entity = self.get_by_id(entity_id)
        if entity is None:
            return False
        self.session.delete(entity)
        self.session.flush()
        return True


@dataclass(frozen=True, slots=True)
class GroupStats:
    name: str
    students: int
    average: float | None
    best: float | None
    worst: float | None


class GroupRepository(Repository[Group]):
    model = Group

    def create(self, name: str) -> Group:
        return self.add(Group(name=name))

    def get_by_name(self, name: str) -> Group | None:
        return self.session.scalar(select(Group).where(Group.name == name))

    def stats(self) -> list[GroupStats]:
        statement = (
            select(
                Group.name,
                func.count(Student.id),
                func.avg(Student.average_grade),
                func.max(Student.average_grade),
                func.min(Student.average_grade),
            )
            .outerjoin(Student, Student.group_id == Group.id)
            .group_by(Group.id, Group.name)
            .order_by(Group.name)
        )
        return [
            GroupStats(
                name,
                int(students),
                None if average is None else float(average),
                None if best is None else float(best),
                None if worst is None else float(worst),
            )
            for name, students, average, best, worst in self.session.execute(statement)
        ]


class StudentRepository(Repository[Student]):
    model = Student

    def find_by_last_name(self, last_name: str) -> list[Student]:
        statement = (
            select(Student)
            .where(Student.last_name == last_name.strip())
            .order_by(Student.first_name, Student.id)
        )
        return list(self.session.scalars(statement))

    def find_by_group(self, group_id: int) -> list[Student]:
        statement = (
            select(Student)
            .where(Student.group_id == group_id)
            .order_by(Student.last_name, Student.first_name, Student.id)
        )
        return list(self.session.scalars(statement))

    def filter_by_group(self, group_name: str) -> list[Student]:
        statement = (
            select(Student)
            .join(Group, Student.group_id == Group.id)
            .where(Group.name == group_name)
            .order_by(Student.last_name, Student.first_name, Student.id)
        )
        return list(self.session.scalars(statement))

    def search(
        self,
        *,
        group_id: int | None = None,
        min_grade: float | None = None,
        max_grade: float | None = None,
        order_by: Literal["last_name", "average_grade"] = "last_name",
        descending: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Student]:
        statement: Select[tuple[Student]] = select(Student)
        if group_id is not None:
            statement = statement.where(Student.group_id == group_id)
        if min_grade is not None:
            statement = statement.where(Student.average_grade >= min_grade)
        if max_grade is not None:
            statement = statement.where(Student.average_grade <= max_grade)
        column = Student.average_grade if order_by == "average_grade" else Student.last_name
        statement = statement.order_by(column.desc() if descending else column.asc(), Student.id)
        return list(self.session.scalars(statement.limit(limit).offset(offset)))

    def update_grade(self, student_id: int, grade: float) -> Student | None:
        student = self.get_by_id(student_id)
        if student is None:
            return None
        student.average_grade = grade
        self.session.flush()
        return student

    def best(self) -> Student | None:
        statement = (
            select(Student)
            .order_by(Student.average_grade.desc(), Student.last_name, Student.id)
            .limit(1)
        )
        return self.session.scalar(statement)

    def group_average(self, group_id: int) -> float | None:
        result = self.session.scalar(
            select(func.avg(Student.average_grade)).where(Student.group_id == group_id)
        )
        return None if result is None else float(result)
