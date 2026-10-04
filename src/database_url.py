"""Keep the established psycopg2 driver for unqualified PostgreSQL URLs."""

from sqlalchemy.engine import URL, make_url


def engine_url(url: str | URL) -> URL:
    """Select psycopg2 only when the caller did not name a PostgreSQL driver.

    SQLAlchemy 2.1 changed the default for ``postgresql://`` to psycopg 3.
    Using URL.set preserves escaped credentials, database names and query
    parameters without altering explicit driver choices or other dialects.
    """
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        return parsed.set(drivername="postgresql+psycopg2")
    return parsed
