# routes/auth.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, get_jwt_identity, get_jwt
from models.user import User
from middleware.auth import require_admin, require_auth
from extensions import db, bcrypt
import re

auth_bp = Blueprint("auth", __name__)

def _make_token(user):
    return create_access_token(identity=str(user.id), additional_claims={"role": user.role})

@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password required"}), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", data["email"]):
        return jsonify({"error": "Invalid email format"}), 400
    if len(data["password"]) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
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
    return jsonify({"token": _make_token(user), "user": user.to_dict()}), 201

@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email", "")).first()
    if not user or not bcrypt.check_password_hash(user.password, data.get("password", "")):
        return jsonify({"error": "Invalid email or password"}), 401
    return jsonify({"token": _make_token(user), "user": user.to_dict()})

@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    uid  = int(get_jwt_identity())
    user = User.query.get_or_404(uid)
    return jsonify(user.to_dict())

# ─── Update profile (name, email, password) ─────────────────────────────────
@auth_bp.route("/profile", methods=["PATCH"])
@require_auth
def update_profile():
    uid  = int(get_jwt_identity())
    user = User.query.get_or_404(uid)
    data = request.get_json()

    if "firstName" in data:
        user.first_name = data["firstName"].strip()
    if "lastName" in data:
        user.last_name = data["lastName"].strip()

    if "email" in data:
        new_email = data["email"].strip().lower()
        if not re.match(r"[^@]+@[^@]+\.[^@]+", new_email):
            return jsonify({"error": "Invalid email format"}), 400
        existing = User.query.filter_by(email=new_email).first()
        if existing and existing.id != uid:
            return jsonify({"error": "Email already in use"}), 409
        user.email = new_email

    if "newPassword" in data:
        current = data.get("currentPassword", "")
        if not bcrypt.check_password_hash(user.password, current):
            return jsonify({"error": "Current password is incorrect"}), 401
        if len(data["newPassword"]) < 6:
            return jsonify({"error": "New password must be at least 6 characters"}), 400
        user.password = bcrypt.generate_password_hash(data["newPassword"]).decode("utf-8")

    db.session.commit()
    # Reissue token in case role changed
    return jsonify({"message": "Profile updated!", "user": user.to_dict(), "token": _make_token(user)})

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
    return jsonify({"message": "Upgraded to premium!", "user": user.to_dict(), "token": _make_token(user)})

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
    return jsonify({"token": _make_token(user), "user": user.to_dict()})