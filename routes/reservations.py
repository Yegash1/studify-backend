# routes/reservations.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt
from models.reservation import Reservation
from models.space import StudySpace
from models.user import User
from middleware.auth import require_auth, require_admin
from extensions import db, socketio
from datetime import datetime

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

# Make a reservation (logged-in user)
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
        notes=data.get("notes", "")
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