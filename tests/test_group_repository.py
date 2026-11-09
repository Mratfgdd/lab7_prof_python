import pytest
from conftest import Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from student_manager.models import Group, Student
from student_manager.repositories import GroupRepository, GroupStats


@pytest.fixture
def groups(session: Session) -> GroupRepository:
    return GroupRepository(session)


def test_create_assigns_primary_key_and_persists_the_group(
    groups: GroupRepository, session: Session, query: Query
) -> None:
    group = groups.create("PI-21")
    session.commit()
    assert group.id is not None
    assert query("SELECT name FROM groups") == [("PI-21",)]


def test_get_by_name_finds_existing_group(groups: GroupRepository) -> None:
    created = groups.create("PI-22")
    assert groups.get_by_name("PI-22") is created


def test_get_by_name_of_unknown_group_is_none(groups: GroupRepository) -> None:
    assert groups.get_by_name("PI-99") is None


def test_get_by_id_returns_group_or_none(groups: GroupRepository) -> None:
    created = groups.create("PI-21")
    assert groups.get_by_id(created.id) is created
    assert groups.get_by_id(999) is None


def test_duplicate_name_violates_unique_constraint(groups: GroupRepository) -> None:
    groups.create("PI-21")
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: groups.name"):
        groups.create("PI-21")


def test_get_all_is_ordered_and_paginated(groups: GroupRepository) -> None:
    for name in ("PI-21", "PI-22", "PI-23", "PI-24"):
        groups.create(name)
    assert [g.name for g in groups.get_all()] == ["PI-21", "PI-22", "PI-23", "PI-24"]
    assert [g.name for g in groups.get_all(limit=2, offset=1)] == ["PI-22", "PI-23"]
    assert groups.get_all(limit=10, offset=10) == []


def test_count_follows_inserts_and_deletes(groups: GroupRepository) -> None:
    assert groups.count() == 0
    first = groups.create("PI-21")
    groups.create("PI-22")
    assert groups.count() == 2
    assert groups.delete(first.id) is True
    assert groups.count() == 1


def test_delete_of_unknown_group_returns_false(groups: GroupRepository) -> None:
    assert groups.delete(404) is False


def test_stats_aggregate_students_per_group_and_keep_empty_groups(
    groups: GroupRepository, session: Session
) -> None:
    first, second = groups.create("PI-21"), groups.create("PI-22")
    groups.create("PI-23")
    session.add_all(
        [
            Student(first_name="Олена", last_name="Коваленко", average_grade=90.0, group=first),
            Student(first_name="Андрій", last_name="Мельник", average_grade=80.0, group=first),
            Student(first_name="Ірина", last_name="Бондар", average_grade=99.5, group=second),
        ]
    )
    session.flush()
    stats = groups.stats()
    assert [(s.name, s.students, s.best, s.worst) for s in stats] == [
        ("PI-21", 2, 90.0, 80.0),
        ("PI-22", 1, 99.5, 99.5),
        ("PI-23", 0, None, None),
    ]
    assert [s.average for s in stats[:2]] == [pytest.approx(85.0), pytest.approx(99.5)]
    assert stats[2].average is None
    assert isinstance(stats[0], GroupStats)


def test_stats_of_empty_database_is_empty(groups: GroupRepository) -> None:
    assert groups.stats() == []


def test_deleting_group_removes_its_students(
    groups: GroupRepository, session: Session, query: Query
) -> None:
    group = groups.create("PI-21")
    session.add(Student(first_name="Олена", last_name="Коваленко", average_grade=90, group=group))
    session.commit()
    assert groups.delete(group.id) is True
    session.commit()
    assert query("SELECT count(*) FROM students") == [(0,)]


def test_group_object_is_a_mapped_entity(groups: GroupRepository) -> None:
    assert isinstance(groups.create("PI-21"), Group)
