from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    max_input_chars: int = 360
    max_output_tokens: int = 800

    # Base de datos
    database_url: str = "postgresql://nexo:nexo_dev@localhost:5432/nexo_db"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""

    # Wompi
    wompi_public_key: str = ""
    wompi_private_key: str = ""
    wompi_events_secret: str = ""

    # Aplicación
    base_url: str = "http://localhost:8000"
    debug: bool = False
    log_level: str = "INFO"
    restaurante_nombre: str = "Tu Restaurante"

    # JWT / Admin
    secret_key: str = "nexo-dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8 horas
    admin_email: str = "admin@nexo.app"
    admin_password: str = ""  # Si se define, se crea el operador al iniciar


settings = Settings()
