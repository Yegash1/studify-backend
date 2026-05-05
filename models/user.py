# models/user.py
from extensions import db

class User(db.Model):
    _tablename_ = "users"
    id              = db.Column(db.Integer,     primary_key=True)
    first_name      = db.Column(db.String(100), nullable=False)
    last_name       = db.Column(db.String(100), nullable=False)
    email           = db.Column(db.String(255), unique=True, nullable=False)
    password        = db.Column(db.String(255), nullable=False)
    role            = db.Column(db.String(20),  default="user")
    is_verified     = db.Column(db.Boolean,     default=False)
    verify_token    = db.Column(db.String(64),  nullable=True)

    def to_dict(self):
        return {
            "id":         self.id,
            "firstName":  self.first_name,
            "lastName":   self.last_name,
            "email":      self.email,
            "role":       self.role,
            "isVerified": self.is_verified
        }