"""Configuration de l'application Floouzz."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Parametres charges depuis les variables d'environnement."""

    # Base de donnees
    DATABASE_URL: str = "postgresql+asyncpg://floouzz:floouzz_secret@db:5432/floouzz"

    # Application
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # Cles API (optionnelles — les sources qui en ont besoin verifient leur presence)
    SERPAPI_KEY: str | None = None
    APIFY_TOKEN: str | None = None
    ANTHROPIC_API_KEY: str | None = None

    # Webhook
    WEBHOOK_TOKEN: str | None = None

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
