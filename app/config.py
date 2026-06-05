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

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    cache_menu_ttl: int = 300   # segundos — TTL del menú formateado
    cache_faq_ttl: int = 3600  # segundos — TTL de respuestas FAQ

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

    # Langfuse (Observabilidad LLM)
    # Obtén tus claves en https://cloud.langfuse.com → Settings → API Keys
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"  # cambia si usas instancia self-hosted

    # JWT / Admin
    secret_key: str = "nexo-dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8 horas
    admin_email: str = "admin@nexo.app"
    admin_password: str = ""  # Si se define, se crea el operador al iniciar
    admin_restaurante_id: str = "default"  # tenant asignado al operador admin inicial


settings = Settings()
