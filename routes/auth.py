# routes/auth.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, get_jwt_identity, get_jwt
from models.user import User
from models.space import StudySpace
from middleware.auth import require_admin, require_auth
from extensions import db, bcrypt

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password required"}), 400
    if User.query.filter_by(email=data["email"]).first():
        return jsonify({"error": "Email already registered"}), 409
    hashed = bcrypt.generate_password_hash(data["password"]).decode("utf-8")
    user = User(
        first_name=data.get("firstName", ""),
        last_name=data.get("lastName", ""),
        email=data["email"],
        password=hashed
    )
    db.session.add(user)
    db.session.commit()
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"token": token, "user": user.to_dict()}), 201

@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email", "")).first()
    if not user or not bcrypt.check_password_hash(user.password, data.get("password", "")):
        return jsonify({"error": "Invalid email or password"}), 401

    # ── Auto-link owner if admin pre-assigned their email to a space ──
    owned_space = StudySpace.query.filter_by(owner_email=user.email, owner_id=None).first()
    if owned_space:
        owned_space.owner_id = user.id
        db.session.commit()

    # ── Promote to owner role if the user owns a space and isn't admin ──
    if user.role not in ("admin",):
        has_space = StudySpace.query.filter(
            (StudySpace.owner_id == user.id) | (StudySpace.owner_email == user.email)
        ).first()
        if has_space and user.role != "owner":
            user.role = "owner"
            db.session.commit()

    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"token": token, "user": user.to_dict()})

@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    uid  = int(get_jwt_identity())
    user = User.query.get_or_404(uid)
    return jsonify(user.to_dict())

@auth_bp.route("/users", methods=["GET"])
@require_admin
def get_users():
    users = User.query.all()
    return jsonify([u.to_dict() for u in users])

@auth_bp.route("/upgrade", methods=["POST"])
@require_auth
def upgrade():
    uid  = int(get_jwt_identity())
    user = User.query.get_or_404(uid)
    user.role = "premium"
    db.session.commit()
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"message": "Upgraded to premium!", "user": user.to_dict(), "token": token})

@auth_bp.route("/profile", methods=["PATCH"])
@require_auth
def update_profile():
    uid  = int(get_jwt_identity())
    user = User.query.get_or_404(uid)
    data = request.get_json()

    first = data.get("firstName", "").strip()
    last  = data.get("lastName",  "").strip()
    email = data.get("email",     "").strip()

    if not first or not last or not email:
        return jsonify({"error": "Name and email are required."}), 400

    # Check email isn't taken by a different account
    existing = User.query.filter_by(email=email).first()
    if existing and existing.id != uid:
        return jsonify({"error": "Email already in use by another account."}), 409

    # Optional password change
    new_pw = data.get("newPassword", "").strip()
    if new_pw:
        current_pw = data.get("currentPassword", "")
        if not bcrypt.check_password_hash(user.password, current_pw):
            return jsonify({"error": "Current password is incorrect."}), 401
        user.password = bcrypt.generate_password_hash(new_pw).decode("utf-8")

    user.first_name = first
    user.last_name  = last
    user.email      = email
    db.session.commit()

    # Re-issue token so role/identity stay fresh
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"user": user.to_dict(), "token": token})


@auth_bp.route("/google", methods=["POST"])
def google_login():
    data  = request.get_json()
    email = data.get("email")
    if not email:
        return jsonify({"error": "Email required"}), 400
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            first_name=data.get("firstName", "Google"),
            last_name=data.get("lastName", "User"),
            email=email,
            password=bcrypt.generate_password_hash("google-oauth").decode("utf-8")
        )
        db.session.add(user)
        db.session.commit()

    # ── Auto-link owner for Google login too ──
    owned_space = StudySpace.query.filter_by(owner_email=user.email, owner_id=None).first()
    if owned_space:
        owned_space.owner_id = user.id
        db.session.commit()

    # ── Promote to owner role if the user owns a space and isn't admin ──
    if user.role not in ("admin",):
        has_space = StudySpace.query.filter(
            (StudySpace.owner_id == user.id) | (StudySpace.owner_email == user.email)
        ).first()
        if has_space and user.role != "owner":
            user.role = "owner"
            db.session.commit()

    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"token": token, "user": user.to_dict()})