import os
from dataclasses import dataclass


def csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    gmail_username: str = os.getenv("GMAIL_USERNAME", "")
    gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
    report_to_email: str = os.getenv("REPORT_TO_EMAIL", "")
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "465"))
    backend_public_url: str = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8000")
    # Docker network shared by the backend and the target containers it deploys.
    docker_network: str = os.getenv("NEXUS_DOCKER_NETWORK", "nexus_net")
    # Timestamps in generated test suite folder names.
    timezone: str = os.getenv("NEXUS_TIMEZONE", "Asia/Kolkata")
    host_suites_dir: str = os.getenv("NEXUS_HOST_SUITES_DIR", "generated_testsuites")
    deploy_timeout_seconds: int = int(os.getenv("NEXUS_DEPLOY_TIMEOUT_SECONDS", "90"))
    cors_origins: list[str] = None

    def __post_init__(self):
        object.__setattr__(self, "cors_origins", csv(os.getenv("CORS_ORIGINS", "http://localhost:8080")))


settings = Settings()
