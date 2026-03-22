# routes/auth.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, get_jwt_identity, get_jwt
from models.user import User
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
        first_name=data["firstName"],
        last_name=data["lastName"],
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
    user = User.query.filter_by(email=data["email"]).first()
    if not user or not bcrypt.check_password_hash(user.password, data["password"]):
        return jsonify({"error": "Invalid email or password"}), 401
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