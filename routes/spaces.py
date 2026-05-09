# routes/spaces.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt
from models.space import StudySpace
from models.user import User
from middleware.auth import require_admin, require_auth
from extensions import db

spaces_bp = Blueprint("spaces", __name__)

@spaces_bp.route("/", methods=["GET"])
def get_spaces():
    cat    = request.args.get("category")
    rating = request.args.get("minRating", 0, type=float)
    q = StudySpace.query
    if cat:    q = q.filter_by(category=cat)
    if rating: q = q.filter(StudySpace.rating >= rating)
    return jsonify([s.to_dict() for s in q.all()])

@spaces_bp.route("/mine", methods=["GET"])
@require_auth
def my_space():
    uid   = int(get_jwt_identity())
    space = StudySpace.query.filter_by(owner_id=uid).first()
    if not space:
        return jsonify({"error": "No space assigned to your account yet. Contact admin."}), 404
    return jsonify(space.to_dict())

@spaces_bp.route("/<int:space_id>", methods=["GET"])
def get_space(space_id):
    space = StudySpace.query.get_or_404(space_id)
    return jsonify(space.to_dict())

@spaces_bp.route("/", methods=["POST"])
@require_admin
def add_space():
    data = request.get_json()

    # ── Check if owner email already has an account ──
    owner_email = data.get("owner_email", None)
    owner_id    = None
    if owner_email:
        existing_user = User.query.filter_by(email=owner_email).first()
        if existing_user:
            owner_id = existing_user.id  # link immediately if account exists

    space = StudySpace(
        name=data["name"],
        category=data["category"],
        location=data["location"],
        total_seats=data.get("seats", 10),
        available=data.get("seats", 10),
        status="open",
        rating=data.get("rating", 4.0),
        hours=data.get("hours", ""),
        price=data.get("price", "Free"),
        emoji=data.get("emoji", "📍"),
        tags=data.get("tags", []),
        owner_email=owner_email,  # save email even if no account yet
        owner_id=owner_id         # link id if account already exists
    )
    db.session.add(space)
    db.session.commit()
    return jsonify(space.to_dict()), 201

@spaces_bp.route("/<int:space_id>", methods=["PUT"])
@require_auth
def update_space(space_id):
    space  = StudySpace.query.get_or_404(space_id)
    claims = get_jwt()
    uid    = int(get_jwt_identity())
    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403
    data = request.get_json()
    for key, val in data.items():
        if hasattr(space, key):
            setattr(space, key, val)
    db.session.commit()
    return jsonify(space.to_dict())

@spaces_bp.route("/<int:space_id>", methods=["DELETE"])
@require_admin
def delete_space(space_id):
    space = StudySpace.query.get_or_404(space_id)
    db.session.delete(space)
    db.session.commit()
    return jsonify({"message": "Deleted successfully"})

@spaces_bp.route("/<int:space_id>/assign-owner", methods=["PATCH"])
@require_admin
def assign_owner(space_id):
    space = StudySpace.query.get_or_404(space_id)
    data  = request.get_json()
    owner_email = data.get("owner_email")
    if not owner_email:
        return jsonify({"error": "owner_email is required"}), 400

    # Save the email regardless
    space.owner_email = owner_email

    # Link owner_id if account already exists
    existing_user = User.query.filter_by(email=owner_email).first()
    if existing_user:
        space.owner_id = existing_user.id

    db.session.commit()
    return jsonify({"message": "Owner assigned!", "space": space.to_dict()})