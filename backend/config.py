from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Gemma Studio"
    app_host: str = "127.0.0.1"
    app_port: int = 8765
    app_data_dir: Path = Path("./data")
    # NoDecode lets the validator accept friendly comma-separated .env values.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    hf_token: str | None = None
    model_id: str = "google/gemma-3-1b-it"
    model_device: str = "cpu"
    model_dtype: str = "float32"
    model_quantization: str = "none"
    max_new_tokens: int = 1024
    model_context_messages: int = 12
    cpu_threads: int = 0
    document_max_chars: int = 24_000
    smart_code_max_context_chars: int = 48_000
    smart_code_max_output_tokens: int = 4096
    estimate_max_output_tokens: int = 3072
    agent_run_retention_days: int = 30
    whisper_model: str = "base.en"
    whisper_compute_type: str = "int8"
    tts_rate: int = 170
    tts_voice: str = "female"
    manim_executable: str = "manim"
    temperature: float = 0.2

    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_story_points_field: str = "customfield_10016"
    jira_write_enabled: bool = False

    image_model_id: str | None = None
    image_inference_steps: int = 8
    phoenix_enabled: bool = True
    phoenix_collector_endpoint: str = "http://127.0.0.1:6006/v1/traces"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [x.strip() for x in value.split(",") if x.strip()]
        return value

    @property
    def uploads_dir(self) -> Path:
        return self.app_data_dir / "uploads"

    @property
    def generated_dir(self) -> Path:
        return self.app_data_dir / "generated"

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
