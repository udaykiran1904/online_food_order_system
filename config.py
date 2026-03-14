import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
SECRET_KEY = "change_this_secret_key"

ADMIN_EMAIL = "admin@foodapp.com"
ADMIN_PASSWORD = "Admin@123"
