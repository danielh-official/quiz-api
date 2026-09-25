from alembic import context

from app.db import engine
from app.models import Base


def include_object(_obj, name, _type, _reflected, _compare_to):
    return name != "kv_store"  # FastMCP OAuth storage manages its own table


with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()
