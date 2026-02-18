"""Configuration settings loaded from .env file."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    telegram_bot_token: str
    allowed_username: str
    claude_cli_path: str
    workspace_dir: str
    data_dir: str
    session_timeout_hours: int = 24

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False
    )

    @property
    def sessions_dir(self) -> Path:
        """Path to sessions directory."""
        return Path(self.data_dir) / "sessions"

    @property
    def media_dir(self) -> Path:
        """Path to media directory."""
        return Path(self.data_dir) / "media"

    @property
    def logs_dir(self) -> Path:
        """Path to logs directory."""
        return Path(self.data_dir) / "logs"


# Global settings instance
settings = Settings()
