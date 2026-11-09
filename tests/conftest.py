import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from student_manager.database import make_engine, make_session_factory
from student_manager.models import Base
from student_manager.services import StudentService

PROJECT_ROOT = Path(__file__).resolve().parents[1]

Query = Callable[..., list[tuple[Any, ...]]]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_students.db"


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def service(session: Session) -> StudentService:
    return StudentService(session)


@pytest.fixture
def school(service: StudentService) -> StudentService:
    service.create_group("PI-21")
    service.create_group("PI-22")
    service.create_group("PI-23")
    service.add_student("Олена", "Коваленко", "PI-21", 95.0, "olena@example.com")
    service.add_student("Андрій", "Мельник", "PI-21", 80.0)
    service.add_student("Ірина", "Бондар", "PI-22", 99.5)
    service.add_student("Тарас", "Гриценко", "PI-22", 55.0)
    return service


@pytest.fixture
def query(db_path: Path) -> Query:
    def run(sql: str, *parameters: object) -> list[tuple[Any, ...]]:
        with closing(sqlite3.connect(db_path)) as connection:
            return connection.execute(sql, parameters).fetchall()

    return run


@pytest.fixture
def group_of(query: Query) -> Callable[[int], str]:
    def read(student_id: int) -> str:
        rows = query(
            "SELECT g.name FROM students s JOIN groups g ON g.id = s.group_id WHERE s.id = ?",
            student_id,
        )
        return str(rows[0][0])

    return read


@pytest.fixture
def alembic_cfg(db_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    config.attributes["configure_logger"] = False
    return config
