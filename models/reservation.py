# models/reservation.py
from extensions import db

class Reservation(db.Model):
    __tablename__ = "reservations"
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"),        nullable=False)
    space_id     = db.Column(db.Integer, db.ForeignKey("study_spaces.id"), nullable=False)
    date         = db.Column(db.Date,    nullable=False)
    start_time   = db.Column(db.Time,    nullable=False)
    duration_hrs = db.Column(db.Integer, nullable=False)
    persons      = db.Column(db.Integer, default=1)
    total_price  = db.Column(db.String(50), default="Free")
    notes        = db.Column(db.Text)
    status       = db.Column(db.String(20), default="confirmed")

    user  = db.relationship("User",      backref="reservations")
    space = db.relationship("StudySpace", backref="reservations")

    def to_dict(self):
        return {
            "id":         self.id,
            "userId":     self.user_id,
            "userName":   f"{self.user.first_name} {self.user.last_name}",
            "placeId":    self.space_id,
            "placeName":  self.space.name,
            "placeEmoji": self.space.emoji,
            "placeLoc":   self.space.location,
            "placeCat":   self.space.category,
            "date":       str(self.date),
            "start":      str(self.start_time),
            "duration":   self.duration_hrs,
            "persons":    self.persons or 1,
            "totalPrice": self.total_price or "Free",
            "notes":      self.notes or "",
            "status":     self.status
        }