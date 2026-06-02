from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key")
APP_NAME = os.getenv("APP_NAME", "Import Đơn Hàng VinMart")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

DATABASE_PATH = "data/app.db"
UPLOAD_DIR = "uploads"
APP_ENV = os.getenv("APP_ENV", "production")
