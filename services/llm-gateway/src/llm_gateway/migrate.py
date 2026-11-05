"""Runs database migrations from code, so the service can upgrade itself at startup."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def _upgrade(url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["url"] = url
    command.upgrade(config, "head")


def upgrade_to_head(url: str) -> None:
    """Blocks until done. Uses its own thread because env.py starts its own event loop."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(_upgrade, url).result()
