import os
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from student_manager.exceptions import SchemaMissingError

DEFAULT_URL = "sqlite:///students.db"
URL_VARIABLE = "STUDENT_MANAGER_DATABASE_URL"


def database_url() -> str:
    return os.environ.get(URL_VARIABLE, DEFAULT_URL)


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    engine = create_engine(url or database_url(), echo=echo)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_schema(engine: Engine) -> None:
    if not inspect(engine).has_table("alembic_version"):
        raise SchemaMissingError("БД не ініціалізовано: виконайте alembic upgrade head")
