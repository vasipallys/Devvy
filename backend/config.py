from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Devvy — Evidence-Based Development"
    app_host: str = "127.0.0.1"
    app_port: int = 8765
    app_data_dir: Path = Path("./data")
    # NoDecode lets the validator accept friendly comma-separated .env values.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Authentication is on by default. Tests and deliberately isolated legacy deployments
    # may disable it explicitly; hosted/non-loopback deployments must keep it enabled.
    auth_enabled: bool = True
    auth_secure_cookies: bool = False
    auth_session_hours: int = 12
    auth_remember_days: int = 30
    auth_login_attempts: int = 5
    auth_login_window_minutes: int = 10
    max_active_jobs_per_user: int = 8

    hf_token: str | None = None
    model_id: str = "google/gemma-3-1b-it"
    model_device: str = "cpu"
    model_dtype: str = "float32"
    model_quantization: str = "none"
    #: Output ceiling for Chat and Talk. 1024 cut ordinary answers mid-word — a request for
    #: an explanatory article reached it every time — and a truncated answer is worse than a
    #: slower one. Generation stays serialized and interruptible, so the cost of the higher
    #: ceiling is only paid by answers that actually need the room. Truncation at any ceiling
    #: is reported rather than hidden.
    max_new_tokens: int = 2048
    model_context_messages: int = 12
    cpu_threads: int = 0
    document_max_chars: int = 24_000
    smart_code_max_context_chars: int = 48_000
    smart_code_max_output_tokens: int = 4096
    estimate_max_output_tokens: int = 3072
    agent_run_retention_days: int = 30
    #: Completed background jobs stay queryable for this long so a returning browser can
    #: still read a result it never collected. Older jobs and their events are purged.
    job_retention_days: int = 7
    #: Uploaded documents and generated artefacts are swept after this long. They are inputs
    #: and outputs of a run, not a library: without a sweep the data directory is the one
    #: part of the application that grows without bound for as long as it is used.
    upload_retention_days: int = 7
    #: How YUKTI addresses the user by default. A stated preference ("call me Vikram") is
    #: kept in the memory bank and always wins over this.
    yukti_address: str = "sir"
    #: The user's notes directory. Empty disables the second brain entirely rather than
    #: guessing at a location — a butler rifling through directories nobody pointed him at is
    #: not a feature.
    yukti_vault_root: str = ""
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


#: Hosts that only the local machine can reach. Anything else is a network deployment, and a
#: network deployment has to satisfy the checks below before the process will serve traffic.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def deployment_problems(settings: Settings) -> tuple[list[str], list[str]]:
    """Configuration that is safe on a laptop and dangerous on a network.

    Every setting here has a sensible local default and a catastrophic remote consequence, and
    nothing in normal operation reveals the difference — the application works perfectly right
    up until the moment the mistake matters. So the coherence between "who can reach this" and
    "how is it protected" is checked once, at startup, and stated out loud.

    Returns (fatal, warnings). Fatal problems stop the process: refusing to start is a bad
    afternoon, whereas serving everyone's conversations and repositories to an unauthenticated
    network is not recoverable by noticing later.
    """
    remote = settings.app_host not in _LOOPBACK_HOSTS
    fatal: list[str] = []
    warnings: list[str] = []

    if remote and not settings.auth_enabled:
        fatal.append(
            f"APP_HOST is {settings.app_host!r}, which is reachable from the network, but "
            "AUTH_ENABLED is false. Every conversation, estimate, and repository path would be "
            "readable and writable by anyone who can reach this port. Set AUTH_ENABLED=true, or "
            "bind to 127.0.0.1."
        )
    if remote and settings.auth_enabled and not settings.auth_secure_cookies:
        warnings.append(
            "AUTH_SECURE_COOKIES is false on a network-reachable host. Session cookies will be "
            "sent over plain HTTP and can be captured in transit. Set AUTH_SECURE_COOKIES=true "
            "and serve over HTTPS (directly or behind a TLS-terminating proxy)."
        )
    if remote:
        warnings.append(
            "Loopback origins are always accepted by CORS. On a network deployment, set "
            "CORS_ORIGINS to the exact origins you serve the frontend from."
        )
    if settings.jira_write_enabled and not settings.auth_enabled:
        warnings.append(
            "JIRA_WRITE_ENABLED is true with authentication disabled: anyone who can reach this "
            "port can write story points into your tracker."
        )
    return fatal, warnings


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
