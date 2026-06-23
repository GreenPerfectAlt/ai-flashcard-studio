from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class ColumnMigration:
    table: str
    name: str
    ddl: str


CARD_COLUMNS: tuple[ColumnMigration, ...] = (
    ColumnMigration("cards", "source_node_id", "ALTER TABLE cards ADD COLUMN source_node_id VARCHAR"),
    ColumnMigration("cards", "tags", "ALTER TABLE cards ADD COLUMN tags VARCHAR"),
    ColumnMigration("cards", "model", "ALTER TABLE cards ADD COLUMN model VARCHAR"),
    ColumnMigration("cards", "due_date", "ALTER TABLE cards ADD COLUMN due_date DATETIME"),
    ColumnMigration("cards", "order", "ALTER TABLE cards ADD COLUMN \"order\" INTEGER DEFAULT 0"),
    ColumnMigration("cards", "x", "ALTER TABLE cards ADD COLUMN x INTEGER"),
    ColumnMigration("cards", "y", "ALTER TABLE cards ADD COLUMN y INTEGER"),
    ColumnMigration("cards", "card_type", "ALTER TABLE cards ADD COLUMN card_type VARCHAR DEFAULT 'basic'"),
    ColumnMigration("cards", "ease_factor", "ALTER TABLE cards ADD COLUMN ease_factor FLOAT DEFAULT 2.5"),
    ColumnMigration("cards", "interval_days", "ALTER TABLE cards ADD COLUMN interval_days INTEGER DEFAULT 0"),
    ColumnMigration("cards", "review_count", "ALTER TABLE cards ADD COLUMN review_count INTEGER DEFAULT 0"),
    ColumnMigration("cards", "lapses", "ALTER TABLE cards ADD COLUMN lapses INTEGER DEFAULT 0"),
    ColumnMigration("cards", "last_reviewed_at", "ALTER TABLE cards ADD COLUMN last_reviewed_at DATETIME"),
    ColumnMigration("cards", "image_path", "ALTER TABLE cards ADD COLUMN image_path VARCHAR"),
    ColumnMigration("cards", "export_profile", "ALTER TABLE cards ADD COLUMN export_profile VARCHAR DEFAULT 'anki'"),
    ColumnMigration("cards", "fields_json", "ALTER TABLE cards ADD COLUMN fields_json VARCHAR"),
)

DECK_COLUMNS: tuple[ColumnMigration, ...] = (
    ColumnMigration("decks", "tags", "ALTER TABLE decks ADD COLUMN tags VARCHAR"),
)

SOURCE_NODE_COLUMNS: tuple[ColumnMigration, ...] = (
    ColumnMigration("source_nodes", "icon", "ALTER TABLE source_nodes ADD COLUMN icon VARCHAR DEFAULT '📄'"),
    ColumnMigration("source_nodes", "preview", "ALTER TABLE source_nodes ADD COLUMN preview VARCHAR"),
    ColumnMigration("source_nodes", "content", "ALTER TABLE source_nodes ADD COLUMN content VARCHAR"),
    ColumnMigration("source_nodes", "media_json", "ALTER TABLE source_nodes ADD COLUMN media_json VARCHAR"),
    ColumnMigration("source_nodes", "tags", "ALTER TABLE source_nodes ADD COLUMN tags VARCHAR"),
    ColumnMigration("source_nodes", "color", "ALTER TABLE source_nodes ADD COLUMN color VARCHAR"),
    ColumnMigration("source_nodes", "url", "ALTER TABLE source_nodes ADD COLUMN url VARCHAR"),
    ColumnMigration("source_nodes", "source_type", "ALTER TABLE source_nodes ADD COLUMN source_type VARCHAR DEFAULT 'text'"),
    ColumnMigration("source_nodes", "x", "ALTER TABLE source_nodes ADD COLUMN x INTEGER DEFAULT 120"),
    ColumnMigration("source_nodes", "y", "ALTER TABLE source_nodes ADD COLUMN y INTEGER DEFAULT 160"),
)

INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_cards_deck_created_order ON cards(deck_id, created_at DESC, \"order\" ASC)",
    "CREATE INDEX IF NOT EXISTS idx_cards_source_node_id ON cards(source_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_due_date ON cards(due_date)",
    "CREATE INDEX IF NOT EXISTS idx_cards_card_type ON cards(card_type)",
    "CREATE INDEX IF NOT EXISTS idx_cards_image_path ON cards(image_path)",
    "CREATE INDEX IF NOT EXISTS idx_cards_export_profile ON cards(export_profile)",
    "CREATE INDEX IF NOT EXISTS idx_source_nodes_deck_created ON source_nodes(deck_id, created_at DESC)",
)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table_name},
    ).fetchone()
    return row is not None


def _column_names(conn, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}


def _apply_columns(conn, migrations: Iterable[ColumnMigration]) -> list[str]:
    applied: list[str] = []
    by_table: dict[str, list[ColumnMigration]] = {}
    for migration in migrations:
        by_table.setdefault(migration.table, []).append(migration)

    for table_name, table_migrations in by_table.items():
        existing = _column_names(conn, table_name)
        if not existing:
            continue
        for migration in table_migrations:
            if migration.name not in existing:
                conn.execute(text(migration.ddl))
                existing.add(migration.name)
                applied.append(f"{migration.table}.{migration.name}")
    return applied


def upgrade_sqlite_schema(engine: Engine) -> list[str]:
    """Idempotently upgrades old local SQLite databases.

    SQLAlchemy create_all() creates missing tables only. It does not add new columns
    to existing tables, so every release that adds a column must include a small
    compatibility migration here.
    """
    applied: list[str] = []
    with engine.begin() as conn:
        applied.extend(_apply_columns(conn, CARD_COLUMNS))
        applied.extend(_apply_columns(conn, DECK_COLUMNS))
        applied.extend(_apply_columns(conn, SOURCE_NODE_COLUMNS))
        for sql in INDEX_SQL:
            try:
                conn.execute(text(sql))
            except Exception:
                pass
    return applied
