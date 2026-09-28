from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    live_poll_seconds: int = 20
    openf1_cache_seconds: int = 20
    open_meteo_cache_seconds: int = 60
    session_cache_seconds: int = 300
    jolpica_base: str = "https://api.jolpi.ca/ergast/f1"
    openf1_base: str = "https://api.openf1.org/v1"
    open_meteo_base: str = "https://api.open-meteo.com/v1/forecast"
    http_timeout: float = 15.0


settings = Settings()
