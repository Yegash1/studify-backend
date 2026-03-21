# config.py
import os
from dotenv import load_dotenv

load_dotenv()  # reads the .env file

class Config:
    SQLALCHEMY_DATABASE_URI    = os.getenv("DATABASE_URL")
    JWT_SECRET_KEY             = os.getenv("JWT_SECRET_KEY")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
