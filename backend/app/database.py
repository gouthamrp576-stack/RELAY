"""Database connections for the RELAY backend."""

from functools import lru_cache

from sqlalchemy import URL, Engine, create_engine, text

from backend.app.config import get_database_settings

from collections.abc import Generator

from sqlalchemy.orm import Session

@lru_cache
def get_engine() -> Engine:
    settings = get_database_settings()

    database_url = URL.create(
        drivername="postgresql+psycopg",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
    )

    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def check_database() -> dict[str, str]:
    """Verify which database and account Python connects to."""
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_database() AS database, "
                "current_user AS username"
            )
        ).mappings().one()

        return dict(row)
    

def get_session() -> Generator[Session, None, None]:
    """Provide a separate database session for each request."""
    with Session(get_engine()) as session:
        yield session