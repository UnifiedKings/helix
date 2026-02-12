from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SECURITY: change in production
    SECRET_KEY: str = "dev-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    COOKIE_NAME: str = "mr_session"

    # Database
    DATABASE_URL: str = "sqlite:///./helix.db"

    # CORS (comma-separated origins). For dev, "*" is allowed.
    CORS_ORIGINS: str = "*"

settings = Settings()
