"""Connexion et session PostgreSQL asynchrone."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.APP_DEBUG)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    """Générateur de session pour l'injection de dépendances FastAPI."""
    async with async_session() as session:
        yield session
