"""Runtime configuration. Secrets come from the environment, never from code."""

from functools import lru_cache
import base64
import hashlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Inbox Agent"
    database_url: str = "sqlite:///./inboxagent.db"
    redis_url: str = ""
    queue_backend: str = "auto"  # auto | redis | postgres
    secret_key: str = "dev-only-change-me"
    token_encryption_key: str = ""
    demo_mode: bool = True
    inline_worker: bool = True
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    public_base_url: str = "http://localhost:8000"
    web_origin: str = "http://localhost:5173"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/accounts/connect/callback"
    pubsub_topic: str = ""
    pubsub_audience: str = ""
    pubsub_service_account: str = ""
    pubsub_verification_token: str = ""
    allow_insecure_pubsub: bool = False

    fcm_project_id: str = ""
    fcm_service_account_file: str = ""

    access_token_days: int = 14

    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def fernet_key(self) -> bytes:
        """Fernet key. Production must set TOKEN_ENCRYPTION_KEY.

        In demo mode an empty key is derived from SECRET_KEY so local setup
        works. The derived key is not safe to use on a shared server.
        """
        if self.token_encryption_key:
            return self.token_encryption_key.encode()
        if not self.demo_mode:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY is required when DEMO_MODE is off")
        digest = hashlib.sha256(self.secret_key.encode()).digest()
        return base64.urlsafe_b64encode(digest)

    def resolved_queue_backend(self) -> str:
        if self.queue_backend in {"redis", "postgres"}:
            return self.queue_backend
        return "redis" if self.redis_url else "postgres"

    def push_token(self) -> str:
        if self.pubsub_verification_token:
            return self.pubsub_verification_token
        if self.demo_mode:
            return "demo-push-token"
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
