"""Alembic schema migrations. The bot and the legacy migration script both call upgrade_database."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent


def alembic_config(db_path: Path | str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    # Tells env.py not to reconfigure logging, which the caller already set up.
    config.attributes["configure_logger"] = False
    return config


def upgrade_database(db_path: Path | str) -> None:
    """Create the database if needed and bring its schema to the latest revision."""
    command.upgrade(alembic_config(db_path), "head")
