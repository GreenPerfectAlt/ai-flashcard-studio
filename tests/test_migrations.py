from pathlib import Path

from sqlalchemy import create_engine, text

from app.db_migrations import upgrade_sqlite_schema


def test_upgrade_adds_study_columns(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE cards (id INTEGER PRIMARY KEY, front VARCHAR, back VARCHAR, deck_id INTEGER, created_at DATETIME)"))
        conn.execute(text("CREATE TABLE decks (id INTEGER PRIMARY KEY, name VARCHAR, created_at DATETIME)"))
        conn.execute(text("CREATE TABLE source_nodes (id VARCHAR PRIMARY KEY, deck_id INTEGER, title VARCHAR, created_at DATETIME)"))
    applied = upgrade_sqlite_schema(engine)
    with engine.connect() as conn:
        card_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(cards)"))}
        deck_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(decks)"))}
    assert "card_type" in card_columns
    assert "ease_factor" in card_columns
    assert "interval_days" in card_columns
    assert "review_count" in card_columns
    assert "lapses" in card_columns
    assert "last_reviewed_at" in card_columns
    assert "tags" in deck_columns
    assert "cards.card_type" in applied
