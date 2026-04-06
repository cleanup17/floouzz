"""Configuration de l'application Floouzz."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Paramètres chargés depuis les variables d'environnement."""

    # Base de données
    DATABASE_URL: str = "postgresql+asyncpg://floouzz:floouzz_secret@db:5432/floouzz"

    # Application
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
