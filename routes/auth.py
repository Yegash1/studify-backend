# routes/auth.py
import secrets
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import create_access_token, get_jwt_identity, get_jwt
from flask_mail import Message
from models.user import User
from middleware.auth import require_admin, require_auth
from extensions import db, bcrypt, mail

auth_bp = Blueprint("auth", __name__)


def send_verification_email(user):
    frontend_url = current_app.config.get("FRONTEND_URL", "http://localhost:5173")
    link = f"{frontend_url}?verify_token={user.verify_token}"
    msg = Message(
        subject="Verify your Studify account",
        recipients=[user.email],
        html=f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto">
          <h2 style="color:#0f1f3d">Welcome to Studify, {user.first_name}! 🎉</h2>
          <p>Thanks for signing up. Please verify your email address to activate your account.</p>
          <a href="{link}" style="display:inline-block;margin:16px 0;padding:12px 28px;
             background:#00bfa5;color:#fff;border-radius:8px;text-decoration:none;font-weight:700">
            Verify Email
          </a>
          <p style="color:#888;font-size:0.85rem">
            Or copy this link:<br>{link}
          </p>
          <p style="color:#aaa;font-size:0.8rem">This link expires in 24 hours.</p>
        </div>
        """
    )
    mail.send(msg)


def send_reset_email(user, token):
    frontend_url = current_app.config.get("FRONTEND_URL", "http://localhost:5173")
    link = f"{frontend_url}?reset_token={token}"
    msg = Message(
        subject="Reset your Studify password",
        recipients=[user.email],
        html=f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto">
          <h2 style="color:#0f1f3d">Reset your password 🔑</h2>
          <p>Hi {user.first_name}, we received a request to reset your password.</p>
          <a href="{link}" style="display:inline-block;margin:16px 0;padding:12px 28px;
             background:#0f1f3d;color:#fff;border-radius:8px;text-decoration:none;font-weight:700">
            Reset Password
          </a>
          <p style="color:#888;font-size:0.85rem">
            Or copy this link:<br>{link}
          </p>
          <p style="color:#aaa;font-size:0.8rem">
            This link expires in 1 hour. If you didn't request this, ignore this email.
          </p>
        </div>
        """
    )
    mail.send(msg)


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password required"}), 400
    if User.query.filter_by(email=data["email"]).first():
        return jsonify({"error": "Email already registered"}), 409
    hashed = bcrypt.generate_password_hash(data["password"]).decode("utf-8")
    verify_token = secrets.token_urlsafe(32)
    user = User(
        first_name=data.get("firstName", ""),
        last_name=data.get("lastName", ""),
        email=data["email"],
        password=hashed,
        verify_token=verify_token,
        is_verified=False
    )
    db.session.add(user)
    db.session.commit()
    try:
        send_verification_email(user)
    except Exception as e:
        current_app.logger.error(f"Email send failed: {e}")
    return jsonify({
        "message": "Account created! Please check your email to verify your account.",
        "requiresVerification": True
    }), 201


@auth_bp.route("/verify-email", methods=["GET"])
def verify_email():
    token = request.args.get("token", "")
    user = User.query.filter_by(verify_token=token).first()
    if not user:
        return jsonify({"error": "Invalid or expired verification link"}), 400
    user.is_verified = True
    user.verify_token = None
    db.session.commit()
    jwt_token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"message": "Email verified!", "token": jwt_token, "user": user.to_dict()})


@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email", "")).first()
    if not user:
        return jsonify({"error": "No account found with that email"}), 404
    if user.is_verified:
        return jsonify({"error": "Account is already verified"}), 400
    user.verify_token = secrets.token_urlsafe(32)
    db.session.commit()
    try:
        send_verification_email(user)
    except Exception as e:
        return jsonify({"error": "Failed to send email"}), 500
    return jsonify({"message": "Verification email resent!"})


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email", "")).first()
    if not user or not bcrypt.check_password_hash(user.password, data.get("password", "")):
        return jsonify({"error": "Invalid email or password"}), 401
    if not user.is_verified:
        return jsonify({
            "error": "Please verify your email before logging in.",
            "requiresVerification": True,
            "email": user.email
        }), 403
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"token": token, "user": user.to_dict()})


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email", "")).first()
    # Always return 200 to prevent email enumeration
    if user:
        token = secrets.token_urlsafe(32)
        user.verify_token = token
        db.session.commit()
        try:
            send_reset_email(user, token)
        except Exception as e:
            current_app.logger.error(f"Reset email failed: {e}")
    return jsonify({"message": "If that email exists, a reset link has been sent."})


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json()
    token = data.get("token", "")
    new_password = data.get("password", "")
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    user = User.query.filter_by(verify_token=token).first()
    if not user:
        return jsonify({"error": "Invalid or expired reset link"}), 400
    user.password = bcrypt.generate_password_hash(new_password).decode("utf-8")
    user.verify_token = None
    user.is_verified = True   # mark verified if they reset via email
    db.session.commit()
    return jsonify({"message": "Password reset successfully! You can now log in."})


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    uid = int(get_jwt_identity())
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
    uid = int(get_jwt_identity())
    user = User.query.get_or_404(uid)
    user.role = "premium"
    db.session.commit()
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"message": "Upgraded to premium!", "user": user.to_dict(), "token": token})


@auth_bp.route("/google", methods=["POST"])
def google_login():
    data = request.get_json()
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
            is_verified=True   # Google accounts are pre-verified
        )
        db.session.add(user)
        db.session.commit()
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role}
    )
    return jsonify({"token": token, "user": user.to_dict()})