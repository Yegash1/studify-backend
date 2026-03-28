# config.py
import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    SQLALCHEMY_DATABASE_URI        = os.getenv("DATABASE_URL")
    JWT_SECRET_KEY                 = os.getenv("JWT_SECRET_KEY")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAIL_SERVER          = 'smtp.gmail.com'
    MAIL_PORT            = 587
    MAIL_USE_TLS         = True
    MAIL_USERNAME        = os.getenv("MAIL_EMAIL")
    MAIL_PASSWORD        = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER  = os.getenv("MAIL_EMAIL")
    MAIL_SUPPRESS_SEND   = os.getenv("MAIL_EMAIL") is None