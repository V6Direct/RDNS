import os
from pathlib import Path
from dotenv import load_dotenv

# Absolute path so all uvicorn workers find the same .env regardless of cwd
load_dotenv(Path(__file__).parent.parent / ".env")

class Settings:
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./rdns.db")

settings = Settings()
