# routes/reservations.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt
from models.reservation import Reservation
from models.space import StudySpace
from models.user import User
from middleware.auth import require_auth, require_admin
from extensions import db, socketio
from datetime import datetime, timedelta
import requests
import os

res_bp = Blueprint("reservations", __name__)

def _send_email(to_email, subject, html):
    """Helper to send email via Resend."""
    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}", "Content-Type": "application/json"},
            json={"from": "Studify <onboarding@resend.dev>", "to": [to_email], "subject": subject, "html": html},
            timeout=10
        )
    except Exception as e:
        print(f"Email error: {e}")

def _email_html(header_color, title, body_html):
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
      <div style="background:#0f1f3d;padding:2rem;border-radius:12px 12px 0 0;text-align:center;">
        <h1 style="color:white;margin:0;font-size:1.8rem;">STUDIFY</h1>
        <p style="color:#a8d8d8;margin:0.5rem 0 0;">Your Study Space, Reserved.</p>
      </div>
      <div style="background:#f0ede8;padding:2rem;border-radius:0 0 12px 12px;">
        <h2 style="color:{header_color};">{title}</h2>
        {body_html}
        <p style="color:#666;">- The Studify Team</p>
      </div>
    </div>"""

def _has_conflict(space_id, date, start_time, duration_hrs, exclude_id=None):
    """Check if a reservation conflicts with existing confirmed/pending ones."""
    from datetime import datetime, timedelta
    start_dt = datetime.combine(date, start_time)
    end_dt   = start_dt + timedelta(hours=duration_hrs)

    existing = Reservation.query.filter(
        Reservation.space_id == space_id,
        Reservation.date     == date,
        Reservation.status.in_(["confirmed", "pending"])
    ).all()

    for r in existing:
        if exclude_id and r.id == exclude_id:
            continue
        r_start = datetime.combine(r.date, r.start_time)
        r_end   = r_start + timedelta(hours=r.duration_hrs)
        # Overlap check
        if start_dt < r_end and end_dt > r_start:
            return True, r
    return False, None

# ─── My reservations (logged-in user) — ALL statuses ───────────────────────
@res_bp.route("/mine", methods=["GET"])
@require_auth
def my_reservations():
    uid = int(get_jwt_identity())
    res = Reservation.query.filter_by(user_id=uid).order_by(Reservation.id.desc()).all()
    return jsonify([r.to_dict() for r in res])

# ─── All reservations (admin only) ─────────────────────────────────────────
@res_bp.route("/", methods=["GET"])
@require_admin
def all_reservations():
    return jsonify([r.to_dict() for r in Reservation.query.order_by(Reservation.id.desc()).all()])

# ─── Space reservations (owner or admin) ───────────────────────────────────
@res_bp.route("/space/<int:space_id>", methods=["GET"])
@require_auth
def space_reservations(space_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    space  = StudySpace.query.get_or_404(space_id)
    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403
    res = Reservation.query.filter_by(space_id=space_id).order_by(Reservation.id.desc()).all()
    return jsonify([r.to_dict() for r in res])

# ─── Make a reservation ─────────────────────────────────────────────────────
@res_bp.route("/", methods=["POST"])
@require_auth
def make_reservation():
    data   = request.get_json()
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    space  = StudySpace.query.get_or_404(data["spaceId"])

    if space.available <= 0:
        return jsonify({"error": "No seats available"}), 400

    persons = int(data.get("persons", 1))
    if persons > space.available:
        return jsonify({"error": f"Only {space.available} seat(s) available"}), 400

    # Parse date and time
    try:
        res_date  = datetime.strptime(data["date"], "%Y-%m-%d").date()
        res_time  = datetime.strptime(data["start"], "%I:%M %p").time()
        duration  = int(data["duration"])
    except Exception:
        return jsonify({"error": "Invalid date or time format"}), 400

    # ── Conflict check ──────────────────────────────────────────────────────
    conflict, conflicting = _has_conflict(space.id, res_date, res_time, duration)
    if conflict:
        c_start = datetime.combine(conflicting.date, conflicting.start_time).strftime("%I:%M %p")
        c_end   = (datetime.combine(conflicting.date, conflicting.start_time) + timedelta(hours=conflicting.duration_hrs)).strftime("%I:%M %p")
        return jsonify({"error": f"This space is already booked from {c_start} to {c_end} on that date. Please choose a different time."}), 409

    # Deduct seats
    space.available -= persons
    if space.available == 0:    space.status = "full"
    elif space.available <= 3:  space.status = "busy"

    user_role = claims.get("role", "user")
    status    = "confirmed" if user_role == "premium" else "pending"

    res = Reservation(
        user_id=uid, space_id=space.id,
        date=res_date, start_time=res_time,
        duration_hrs=duration, persons=persons,
        total_price=data.get("totalPrice", "Free"),
        notes=data.get("notes", ""),
        status=status
    )
    db.session.add(res)
    db.session.commit()

    socketio.emit("availability_update", {
        "spaceId": space.id, "available": space.available, "status": space.status
    })

    # Notify user of pending/confirmed
    student = User.query.get(uid)
    if status == "confirmed":
        _send_email(student.email, f"Reservation Confirmed - {space.name}",
            _email_html("#0f1f3d", "Reservation Confirmed! ✅", f"""
            <p>Hi <strong>{student.first_name}</strong>,</p>
            <p>Your reservation at <strong>{space.name}</strong> has been confirmed!</p>
            <div style="background:white;border-radius:12px;padding:1.2rem;margin:1.2rem 0;">
              <p><strong>Date:</strong> {res_date}</p><p><strong>Time:</strong> {res_time}</p>
              <p><strong>Duration:</strong> {duration} hour(s)</p><p><strong>Persons:</strong> {persons}</p>
              <p><strong>Price:</strong> {data.get('totalPrice','Free')}</p>
            </div><p style="color:#666;">Please arrive on time!</p>"""))
    else:
        _send_email(student.email, f"Reservation Pending - {space.name}",
            _email_html("#0f1f3d", "Reservation Received! ⏳", f"""
            <p>Hi <strong>{student.first_name}</strong>,</p>
            <p>Your reservation at <strong>{space.name}</strong> is pending approval by the space owner.</p>
            <div style="background:white;border-radius:12px;padding:1.2rem;margin:1.2rem 0;">
              <p><strong>Date:</strong> {res_date}</p><p><strong>Time:</strong> {res_time}</p>
              <p><strong>Duration:</strong> {duration} hour(s)</p><p><strong>Persons:</strong> {persons}</p>
            </div><p style="color:#666;">We'll notify you once it's confirmed.</p>"""))

    return jsonify(res.to_dict()), 201

# ─── Cancel a reservation ───────────────────────────────────────────────────
@res_bp.route("/<int:res_id>/cancel", methods=["PATCH"])
@require_auth
def cancel_reservation(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)
    if res.user_id != uid and claims.get("role") not in ("admin", "owner"):
        return jsonify({"error": "Not authorized"}), 403
    if res.status in ("cancelled", "rejected"):
        return jsonify({"error": "Already cancelled"}), 400
    res.status = "cancelled"
    res.space.available += res.persons or 1
    if res.space.available > 0: res.space.status = "open"
    db.session.commit()

    # Notify user
    student = User.query.get(res.user_id)
    _send_email(student.email, f"Reservation Cancelled - {res.space.name}",
        _email_html("#d94f2b", "Reservation Cancelled", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>Your reservation at <strong>{res.space.name}</strong> on <strong>{res.date}</strong> has been cancelled.</p>
        <p style="color:#666;">You can make a new booking anytime on Studify.</p>"""))

    return jsonify({"message": "Cancelled"})

# ─── Confirm reservation (owner or admin) ───────────────────────────────────
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

    student = User.query.get(res.user_id)
    _send_email(student.email, f"Reservation Confirmed - {space.name}",
        _email_html("#0f1f3d", "Reservation Confirmed! ✅", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>Your reservation at <strong>{space.name}</strong> has been confirmed!</p>
        <div style="background:white;border-radius:12px;padding:1.2rem;margin:1.2rem 0;">
          <p><strong>Date:</strong> {res.date}</p><p><strong>Time:</strong> {res.start_time}</p>
          <p><strong>Duration:</strong> {res.duration_hrs} hour(s)</p>
          <p><strong>Persons:</strong> {res.persons or 1}</p>
          <p><strong>Price:</strong> {res.total_price or 'Free'}</p>
        </div><p style="color:#666;">Please arrive on time. See you there!</p>"""))

    return jsonify({"message": "Confirmed and student notified!"})

# ─── Reject reservation (owner or admin) ────────────────────────────────────
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

    student = User.query.get(res.user_id)
    _send_email(student.email, f"Reservation Update - {space.name}",
        _email_html("#d94f2b", "Reservation Not Available", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>Unfortunately your reservation at <strong>{space.name}</strong> on <strong>{res.date}</strong> could not be accommodated.</p>
        <p>Please try booking a different time or space on Studify.</p>
        <p style="color:#666;">We're sorry for the inconvenience.</p>"""))

    return jsonify({"message": "Rejected and student notified!"})

# ─── Owner analytics ────────────────────────────────────────────────────────
@res_bp.route("/analytics/<int:space_id>", methods=["GET"])
@require_auth
def analytics(space_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    space  = StudySpace.query.get_or_404(space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    reservations = Reservation.query.filter_by(space_id=space_id).all()

    # ── Revenue by day ──────────────────────────────────────────────────────
    revenue_by_day = {}
    for r in reservations:
        if r.status != "confirmed":
            continue
        day = str(r.date)
        try:
            price = float(str(r.total_price).replace("₱", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            price = 0.0
        revenue_by_day[day] = revenue_by_day.get(day, 0.0) + price

    # ── Bookings by hour ────────────────────────────────────────────────────
    bookings_by_hour = {}
    for r in reservations:
        if r.status == "cancelled" or r.status == "rejected":
            continue
        hour = r.start_time.hour
        bookings_by_hour[hour] = bookings_by_hour.get(hour, 0) + 1

    # ── Status breakdown ────────────────────────────────────────────────────
    status_breakdown = {}
    for r in reservations:
        status_breakdown[r.status] = status_breakdown.get(r.status, 0) + 1

    # ── Totals ──────────────────────────────────────────────────────────────
    total_revenue = sum(revenue_by_day.values())
    confirmed     = [r for r in reservations if r.status == "confirmed"]
    avg_duration  = (
        round(sum(r.duration_hrs for r in confirmed) / len(confirmed), 1)
        if confirmed else 0
    )

    return jsonify({
        "revenue_by_day":    [{"date": d, "revenue": round(v, 2)} for d, v in sorted(revenue_by_day.items())],
        "bookings_by_hour":  [{"hour": h, "count": c} for h, c in sorted(bookings_by_hour.items())],
        "status_breakdown":  status_breakdown,
        "total_revenue":     round(total_revenue, 2),
        "avg_duration":      avg_duration,
        "total_bookings":    len(reservations),
        "confirmed_bookings": len(confirmed)
    })