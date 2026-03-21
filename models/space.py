# models/space.py
from extensions import db
from sqlalchemy.dialects.postgresql import ARRAY

class StudySpace(db.Model):
    __tablename__ = "study_spaces"
    id          = db.Column(db.Integer,      primary_key=True)
    name        = db.Column(db.String(255),  nullable=False)
    category    = db.Column(db.String(50))
    location    = db.Column(db.String(255))
    total_seats = db.Column(db.Integer,      default=10)
    available   = db.Column(db.Integer,      default=10)
    status      = db.Column(db.String(20),   default="open")
    rating      = db.Column(db.Numeric(2,1), default=4.0)
    hours       = db.Column(db.String(100))
    price       = db.Column(db.String(50),   default="Free")
    emoji       = db.Column(db.String(10),   default="📍")
    tags        = db.Column(ARRAY(db.String))
    owner_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    owner_email = db.Column(db.String(255), nullable=True)

    def to_dict(self):
        return {
            "id":          self.id,
            "name":        self.name,
            "cat":         self.category,
            "loc":         self.location,
            "seats":       self.available,
            "avail":       self.status,
            "total_seats": self.total_seats or 10,
            "rating":      float(self.rating),
            "hours":       self.hours,
            "price":       self.price,
            "emoji":       self.emoji,
            "tags":        self.tags or [],
            "owner_id":    self.owner_id,
            "owner_email": self.owner_email,
        }