# routes/auth.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, get_jwt_identity, get_jwt
from models.user import User
from middleware.auth import require_admin, require_auth
from extensions import db, bcrypt
import re
import secrets
import os
import requests as req

auth_bp = Blueprint("auth", __name__)


def _make_token(user):
    return create_access_token(identity=str(user.id), additional_claims={"role": user.role})


def _send_verification_email(to_email, first_name, token):
    verify_url = f"{os.getenv('FRONTEND_URL')}/index.html?verify_token={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
      <div style="background:#0f1f3d;padding:2rem;border-radius:12px 12px 0 0;text-align:center;">
        <h1 style="color:white;margin:0;font-size:1.8rem;">STUDIFY</h1>
        <p style="color:#a8d8d8;margin:0.5rem 0 0;">Your Study Space, Reserved.</p>
      </div>
      <div style="background:#f0ede8;padding:2rem;border-radius:0 0 12px 12px;">
        <h2 style="color:#0f1f3d;">Verify your email</h2>
        <p>Hi <strong>{first_name}</strong>, thanks for signing up!</p>
        <p>Click the button below to activate your account:</p>
        <a href="{verify_url}" style="display:inline-block;margin:1.2rem 0;padding:0.8rem 2rem;background:#0f1f3d;color:white;border-radius:8px;text-decoration:none;font-weight:bold;">Verify my email</a>
        <p style="color:#888;font-size:0.85rem;">This link expires in 24 hours. If you didn't sign up, ignore this email.</p>
        <p style="color:#666;">- The Studify Team</p>
      </div>
    </div>"""
    try:
        req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}", "Content-Type": "application/json"},
            json={"from": "Studify <onboarding@resend.dev>", "to": [to_email], "subject": "Verify your Studify account", "html": html},
            timeout=10
        )
    except Exception as e:
        print(f"Verification email error: {e}")


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
        password=hashed,
        is_verified=True,   # Auto-verified until a sending domain is configured
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


@auth_bp.route("/verify-email", methods=["GET"])
def verify_email():
    token = request.args.get("token")
    if not token:
        return jsonify({"error": "Missing token"}), 400

    user = User.query.filter_by(verify_token=token).first()
    if not user:
        return jsonify({"error": "Invalid or expired verification link"}), 404

    user.is_verified  = True
    user.verify_token = None
    db.session.commit()

    return jsonify({
        "message": "Email verified! You can now log in.",
        "token":   _make_token(user),
        "user":    user.to_dict()
    })


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
            password=bcrypt.generate_password_hash("google-oauth").decode("utf-8"),
            is_verified=True   # Google already verified the email
        )
        db.session.add(user)
        db.session.commit()
    return jsonify({"token": _make_token(user), "user": user.to_dict()})