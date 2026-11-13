import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    APP_NAME = os.getenv("APP_NAME", "Cold Email Automation")
    DATA_DIR = BASE_DIR / "data"
    HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
    HUNTER_MAX_RETRIES = int(os.getenv("HUNTER_MAX_RETRIES", "3"))
    HUNTER_RETRY_BASE_DELAY = int(os.getenv("HUNTER_RETRY_BASE_DELAY", "2"))
    HUNTER_REQUEST_TIMEOUT = int(os.getenv("HUNTER_REQUEST_TIMEOUT", "30"))
    BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))
    MAX_CONCURRENT_HUNTER_REQUESTS = int(os.getenv("MAX_CONCURRENT_HUNTER_REQUESTS", "5"))
    MAX_CONCURRENT_AI_REQUESTS = int(os.getenv("MAX_CONCURRENT_AI_REQUESTS", "2"))
    MAX_CONCURRENT_EMAIL_SENDS = int(os.getenv("MAX_CONCURRENT_EMAIL_SENDS", "2"))
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    OLLAMA_CHAT_MAX_TOKENS = int(os.getenv("OLLAMA_CHAT_MAX_TOKENS", "96"))
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "Your Name")
    MAIL_FROM_COMPANY = os.getenv("MAIL_FROM_COMPANY", "")
    MAIL_FROM_TITLE = os.getenv("MAIL_FROM_TITLE", "")
    EXPORTS_DIR = BASE_DIR / "exports"
    LOGS_DIR = BASE_DIR / "logs"
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

settings = Settings()
