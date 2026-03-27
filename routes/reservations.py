# routes/reservations.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt
from models.reservation import Reservation
from models.space import StudySpace
from models.user import User
from middleware.auth import require_auth, require_admin
from extensions import db, socketio
from datetime import datetime
import requests
import os

res_bp = Blueprint("reservations", __name__)

# My reservations (logged-in user)
@res_bp.route("/mine", methods=["GET"])
@require_auth
def my_reservations():
    uid = int(get_jwt_identity())
    res = Reservation.query.filter_by(user_id=uid).all()
    return jsonify([r.to_dict() for r in res])

# All reservations (admin only)
@res_bp.route("/", methods=["GET"])
@require_admin
def all_reservations():
    return jsonify([r.to_dict() for r in Reservation.query.all()])

# Get reservations for a specific space (owner or admin)
@res_bp.route("/space/<int:space_id>", methods=["GET"])
@require_auth
def space_reservations(space_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    space  = StudySpace.query.get_or_404(space_id)
    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403
    res = Reservation.query.filter_by(space_id=space_id).all()
    return jsonify([r.to_dict() for r in res])

# Make a reservation (logged-in user) — starts as "pending"
@res_bp.route("/", methods=["POST"])
@require_auth
def make_reservation():
    data  = request.get_json()
    uid   = int(get_jwt_identity())
    space = StudySpace.query.get_or_404(data["spaceId"])
    if space.available <= 0:
        return jsonify({"error": "No seats available"}), 400

    persons = int(data.get("persons", 1))
    if persons > space.available:
        return jsonify({"error": f"Only {space.available} seat(s) available"}), 400

    space.available -= persons
    if space.available == 0:   space.status = "full"
    elif space.available <= 3: space.status = "busy"

    res = Reservation(
        user_id=uid, space_id=space.id,
        date=datetime.strptime(data["date"], "%Y-%m-%d").date(),
        start_time=datetime.strptime(data["start"], "%I:%M %p").time(),
        duration_hrs=int(data["duration"]),
        persons=persons,
        total_price=data.get("totalPrice", "Free"),
        notes=data.get("notes", ""),
        status="pending"
    )
    db.session.add(res)
    db.session.commit()
    socketio.emit("availability_update", {
        "spaceId":   space.id,
        "available": space.available,
        "status":    space.status
    })
    return jsonify(res.to_dict()), 201

# Cancel a reservation
@res_bp.route("/<int:res_id>/cancel", methods=["PATCH"])
@require_auth
def cancel_reservation(res_id):
    uid = int(get_jwt_identity())
    res = Reservation.query.get_or_404(res_id)
    claims = get_jwt()
    if res.user_id != uid and claims.get("role") != "admin":
        return jsonify({"error": "Not authorized"}), 403
    res.status = "cancelled"
    res.space.available += res.persons or 1
    if res.space.available > 0: res.space.status = "open"
    db.session.commit()
    return jsonify({"message": "Cancelled"})

# Confirm reservation (owner only)
@res_bp.route("/<int:res_id>/confirm", methods=["PATCH"])
@require_auth
def confirm_reservation(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)
    space  = StudySpace.query.get(res.space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    res.status = "confirmed"
    db.session.commit()

    try:
        student = User.query.get(res.user_id)
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}", "Content-Type": "application/json"},
            json={
                "from": "Studify <onboarding@resend.dev>",
                "to": [student.email],
                "subject": f"Reservation Confirmed - {space.name}",
                "html": f"""
                <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
                  <div style="background:#0f1f3d;padding:2rem;border-radius:12px 12px 0 0;text-align:center;">
                    <h1 style="color:white;margin:0;font-size:1.8rem;">STUDIFY</h1>
                    <p style="color:#a8d8d8;margin:0.5rem 0 0;">Your Study Space, Reserved.</p>
                  </div>
                  <div style="background:#f0ede8;padding:2rem;border-radius:0 0 12px 12px;">
                    <h2 style="color:#0f1f3d;">Reservation Confirmed!</h2>
                    <p>Hi <strong>{student.first_name}</strong>,</p>
                    <p>Your reservation at <strong>{space.name}</strong> has been confirmed!</p>
                    <div style="background:white;border-radius:12px;padding:1.2rem;margin:1.2rem 0;">
                      <p><strong>Space:</strong> {space.name}</p>
                      <p><strong>Date:</strong> {res.date}</p>
                      <p><strong>Time:</strong> {res.start_time}</p>
                      <p><strong>Duration:</strong> {res.duration_hrs} hour(s)</p>
                      <p><strong>Persons:</strong> {res.persons or 1}</p>
                      <p><strong>Price:</strong> {res.total_price or 'Free'}</p>
                    </div>
                    <p style="color:#666;">Please arrive on time. See you there!</p>
                    <p style="color:#666;">- The Studify Team</p>
                  </div>
                </div>
                """
            }, timeout=10
        )
    except Exception as e:
        print(f"Email error: {e}")

    return jsonify({"message": "Confirmed and student notified!"})


# Reject reservation (owner only)
@res_bp.route("/<int:res_id>/reject", methods=["PATCH"])
@require_auth
def reject_reservation(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)
    space  = StudySpace.query.get(res.space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    res.status = "rejected"
    space.available += res.persons or 1
    if space.available > 0: space.status = "open"
    db.session.commit()

    try:
        student = User.query.get(res.user_id)
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}", "Content-Type": "application/json"},
            json={
                "from": "Studify <onboarding@resend.dev>",
                "to": [student.email],
                "subject": f"Reservation Update - {space.name}",
                "html": f"""
                <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
                  <div style="background:#0f1f3d;padding:2rem;border-radius:12px 12px 0 0;text-align:center;">
                    <h1 style="color:white;margin:0;font-size:1.8rem;">STUDIFY</h1>
                    <p style="color:#a8d8d8;margin:0.5rem 0 0;">Your Study Space, Reserved.</p>
                  </div>
                  <div style="background:#f0ede8;padding:2rem;border-radius:0 0 12px 12px;">
                    <h2 style="color:#d94f2b;">Reservation Not Available</h2>
                    <p>Hi <strong>{student.first_name}</strong>,</p>
                    <p>Unfortunately your reservation at <strong>{space.name}</strong> on <strong>{res.date}</strong> could not be accommodated.</p>
                    <p>Please try booking a different time or space on Studify.</p>
                    <p style="color:#666;">We're sorry for the inconvenience.</p>
                    <p style="color:#666;">- The Studify Team</p>
                  </div>
                </div>
                """
            }, timeout=10
        )
    except Exception as e:
        print(f"Email error: {e}")

    return jsonify({"message": "Rejected and student notified!"})