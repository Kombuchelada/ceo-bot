from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_bot_token: str
    discord_allowed_user_ids: str = ""
    discord_log_channel_id: int | None = None

    anthropic_api_key: str
    anthropic_model: str = "claude-opus-4-7"

    do_spaces_endpoint: str
    do_spaces_region: str
    do_spaces_bucket: str
    do_spaces_access_key: str
    do_spaces_secret_key: str

    database_path: Path = Field(default=Path("/data/bot.db"))

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8765/oauth/callback"

    token_encryption_key: str

    enable_media_ocr: bool = True
    enable_audio_transcription: bool = True

    log_level: str = "INFO"

    @property
    def allowed_user_ids(self) -> set[int]:
        return {int(x) for x in self.discord_allowed_user_ids.split(",") if x.strip()}


settings = Settings()  # type: ignore[call-arg]
