# routes/ratings.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity
from models.space import StudySpace
from middleware.auth import require_auth
from extensions import db

ratings_bp = Blueprint("ratings", __name__)

@ratings_bp.route("/", methods=["POST"])
@require_auth
def submit_rating():
    data     = request.get_json()
    uid      = int(get_jwt_identity())
    space_id = data.get("space_id")
    stars    = int(data.get("stars", 0))

    if not space_id or not (1 <= stars <= 5):
        return jsonify({"error": "Invalid rating data"}), 400

    space = StudySpace.query.get_or_404(space_id)

    # Check if user already rated — update if so
    existing = db.session.execute(
        db.text("SELECT id FROM ratings WHERE user_id=:uid AND space_id=:sid"),
        {"uid": uid, "sid": space_id}
    ).fetchone()

    if existing:
        db.session.execute(
            db.text("UPDATE ratings SET stars=:stars, comment=:comment WHERE user_id=:uid AND space_id=:sid"),
            {"stars": stars, "comment": data.get("comment",""), "uid": uid, "sid": space_id}
        )
    else:
        db.session.execute(
            db.text("INSERT INTO ratings (user_id, space_id, stars, comment) VALUES (:uid,:sid,:stars,:comment)"),
            {"uid": uid, "sid": space_id, "stars": stars, "comment": data.get("comment","")}
        )

    # Recalculate average rating for the space
    result = db.session.execute(
        db.text("SELECT AVG(stars)::numeric(2,1), COUNT(*) FROM ratings WHERE space_id=:sid"),
        {"sid": space_id}
    ).fetchone()

    new_rating    = float(result[0]) if result[0] else stars
    review_count  = int(result[1])
    space.rating  = round(new_rating, 1)
    db.session.commit()

    return jsonify({
        "message":      "Rating submitted!",
        "new_rating":   round(new_rating, 1),
        "review_count": review_count
    }), 201