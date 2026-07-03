"""Alembic runner used by development startup."""

from __future__ import annotations

from pathlib import Path


def run_migrations() -> None:
    """Run Alembic upgrade head using the repo-local alembic.ini."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    run_migrations()
