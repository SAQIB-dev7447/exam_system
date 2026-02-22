"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "")
SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
PASS_PERCENTAGE: int = int(os.getenv("PASS_PERCENTAGE", "40"))

# PORT is handled dynamically in main.py but kept here for reference
PORT: int = int(os.getenv("PORT", "8000"))
