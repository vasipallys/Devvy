from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON, event
from sqlmodel import Field, Session, SQLModel, create_engine, select

from backend.config import get_settings


def now() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime | None) -> str | None:
    """Serialise a stored timestamp as an unambiguous UTC instant.

    SQLite has no timezone type, so a `datetime` written as aware comes back naive. Emitting
    that bare string makes browsers parse it as *local* time, which silently shifts every
    displayed timestamp by the viewer's offset ("5h ago" for something that just happened).
    Timestamps are always stored in UTC, so re-attaching the offset here is the correct read.
    """
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


class Conversation(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title: str = "New conversation"
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Message(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    conversation_id: UUID = Field(foreign_key="conversation.id", index=True)
    role: str
    content: str
    created_at: datetime = Field(default_factory=now)
    attachments: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    message_metadata: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))


settings = get_settings()
engine = create_engine(
    f"sqlite:///{(settings.app_data_dir / 'gemma_studio.db').as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def configure_sqlite(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    # WAL keeps its durability guarantee against process crashes at NORMAL; only a power
    # loss can cost the last commits. The job runner commits on every progress update,
    # event, and output flush, so an fsync per commit is the difference between a write
    # costing microseconds and costing milliseconds — on the hot path of every run.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def init_db() -> None:
    """Create missing tables, then bring existing ones up to the current schema.

    Order matters. ``create_all`` handles a fresh install, where every table arrives with
    today's columns. Migrations handle an upgrade, where the tables already exist and
    ``create_all`` would leave them untouched — including any column added since.
    """
    # Imported here so every table is registered on SQLModel.metadata before create_all.
    from backend import estimate_history, jobs  # noqa: F401
    from backend.migrations import run_migrations

    SQLModel.metadata.create_all(engine)
    run_migrations(engine)


def create_conversation(session: Session, title: str = "New conversation") -> Conversation:
    item = Conversation(title=title)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def list_messages(session: Session, conversation_id: UUID) -> list[Message]:
    statement = (
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    )
    return list(session.exec(statement).all())
