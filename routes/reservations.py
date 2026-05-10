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

# ─── Email helpers ───────────────────────────────────────────────────────────

def _send_email(to_email, subject, html):
    """Helper to send email via Resend."""
    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}",
                "Content-Type": "application/json"
            },
            json={
                "from": "Studify <onboarding@resend.dev>",
                "to": [to_email],
                "subject": subject,
                "html": html
            },
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


def _reservation_details_html(res):
    """Reusable reservation detail block for emails."""
    return f"""
    <div style="background:white;border-radius:12px;padding:1.2rem;margin:1.2rem 0;">
      <p><strong>Date:</strong> {res.date}</p>
      <p><strong>Time:</strong> {res.start_time}</p>
      <p><strong>Duration:</strong> {res.duration_hrs} hour(s)</p>
      <p><strong>Persons:</strong> {res.persons or 1}</p>
      <p><strong>Price:</strong> {res.total_price or 'Free'}</p>
      <p><strong>Payment:</strong> {(res.payment_method or 'on_arrival').replace('_', ' ').title()}</p>
    </div>"""


# ─── Conflict check ──────────────────────────────────────────────────────────

def _has_conflict(space_id, date, start_time, duration_hrs, exclude_id=None, persons_needed=1):
    """
    Check overlap against confirmed + awaiting_payment + payment_review reservations.
    Pending reservations are NOT counted — seats aren't held until owner approves.
    Returns True only if booked persons + persons_needed exceeds total_seats.
    """
    from models.space import StudySpace
    space    = StudySpace.query.get(space_id)
    total    = space.total_seats or 10

    start_dt = datetime.combine(date, start_time)
    end_dt   = start_dt + timedelta(hours=duration_hrs)

    existing = Reservation.query.filter(
        Reservation.space_id == space_id,
        Reservation.date     == date,
        Reservation.status.in_(["confirmed", "awaiting_payment", "payment_review"])
    ).all()

    booked = 0
    last_overlap = None
    for r in existing:
        if exclude_id and r.id == exclude_id:
            continue
        r_start = datetime.combine(r.date, r.start_time)
        r_end   = r_start + timedelta(hours=r.duration_hrs)
        if start_dt < r_end and end_dt > r_start:
            booked += (r.persons or 1)
            last_overlap = r

    if booked + persons_needed > total:
        return True, last_overlap, booked, total
    return False, None, booked, total


# ─── My reservations (logged-in user) ───────────────────────────────────────

@res_bp.route("/mine", methods=["GET"])
@require_auth
def my_reservations():
    uid = int(get_jwt_identity())
    res = Reservation.query.filter_by(user_id=uid).order_by(Reservation.id.desc()).all()
    return jsonify([r.to_dict() for r in res])


# ─── All reservations (admin only) ──────────────────────────────────────────

@res_bp.route("/", methods=["GET"])
@require_admin
def all_reservations():
    return jsonify([r.to_dict() for r in Reservation.query.order_by(Reservation.id.desc()).all()])


# ─── Space reservations (owner or admin) ────────────────────────────────────

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


# ─── Make a reservation (Stage 1: booking request, no payment yet) ──────────
#
#  Flow:
#    POST /reservations/          → status: pending      (seats NOT deducted)
#    PATCH /:id/confirm           → status: confirmed (pay on arrival)
#                                         OR awaiting_payment (gcash/maya)
#    PATCH /:id/reject            → status: rejected      (seats untouched, no refund needed)
#    PATCH /:id/payment           → status: payment_review
#    PATCH /:id/verify-payment    → status: confirmed     (seats deducted HERE)
#    PATCH /:id/reject-payment    → status: awaiting_payment (back to waiting)
#    PATCH /:id/cancel            → status: cancelled     (seats restored only if they were deducted)

@res_bp.route("/", methods=["POST"])
@require_auth
def make_reservation():
    data   = request.get_json()
    uid    = int(get_jwt_identity())
    space  = StudySpace.query.get_or_404(data["spaceId"])

    if space.available <= 0:
        return jsonify({"error": "No seats available"}), 400

    persons = int(data.get("persons", 1))
    if persons > space.available:
        return jsonify({"error": f"Only {space.available} seat(s) available"}), 400

    try:
        res_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
        res_time = datetime.strptime(data["start"], "%I:%M %p").time()
        duration = int(data["duration"])
    except Exception:
        return jsonify({"error": "Invalid date or time format"}), 400

    payment_method = data.get("paymentMethod", "on_arrival")
    if payment_method not in ("gcash", "maya", "on_arrival"):
        return jsonify({"error": "Invalid payment method"}), 400

    # Conflict check — only blocks if booked persons + new request exceeds total_seats
    conflict, conflicting, booked, total = _has_conflict(space.id, res_date, res_time, duration, persons_needed=persons)
    if conflict:
        seats_left = total - booked
        return jsonify({
            "error": f"Not enough seats for that time slot. {seats_left} seat(s) available, you requested {persons}."
        }), 409

    student = User.query.get(uid)
    is_premium = student.role == "premium"

    # ── Premium users: auto-confirm immediately (skip owner approval) ─────────
    if is_premium:
        if space.available < persons:
            return jsonify({"error": "Not enough seats available"}), 400

        if payment_method == "on_arrival":
            initial_status = "confirmed"
        else:
            initial_status = "awaiting_payment"

        res = Reservation(
            user_id=uid,
            space_id=space.id,
            date=res_date,
            start_time=res_time,
            duration_hrs=duration,
            persons=persons,
            total_price=data.get("totalPrice", "Free"),
            payment_method=payment_method,
            notes=data.get("notes", ""),
            status=initial_status
        )
        db.session.add(res)

        # Deduct seats immediately for premium users
        space.available -= persons
        if space.available == 0:   space.status = "full"
        elif space.available <= 3: space.status = "busy"

        db.session.commit()

        socketio.emit("availability_update", {
            "spaceId": space.id,
            "available": space.available,
            "status": space.status
        })

        if payment_method == "on_arrival":
            _send_email(
                student.email,
                f"Reservation Confirmed - {space.name}",
                _email_html("#1a7a4a", "Reservation Auto-Confirmed! ⭐✅", f"""
                <p>Hi <strong>{student.first_name}</strong>,</p>
                <p>As a Premium member, your reservation at <strong>{space.name}</strong> has been <strong>automatically confirmed</strong> — no waiting needed!</p>
                {_reservation_details_html(res)}
                <p style="color:#666;">Please arrive on time and pay at the venue. See you there!</p>""")
            )
        else:
            method_label = "GCash" if payment_method == "gcash" else "Maya"
            pay_number   = space.gcash_number if payment_method == "gcash" else space.maya_number
            _send_email(
                student.email,
                f"Booking Auto-Confirmed — Please Send Payment - {space.name}",
                _email_html("#1a7a4a", "Booking Auto-Confirmed! Now Send Payment ⭐💳", f"""
                <p>Hi <strong>{student.first_name}</strong>,</p>
                <p>As a Premium member, your booking at <strong>{space.name}</strong> has been <strong>automatically confirmed</strong>!</p>
                {_reservation_details_html(res)}
                <p>Please send payment via <strong>{method_label}</strong> to <strong>{pay_number or 'the number provided by the owner'}</strong> and upload your receipt in the app.</p>""")
            )

        return jsonify({**res.to_dict(), "auto_confirmed": True}), 201

    # ── Regular users: status pending, awaiting owner approval ───────────────
    res = Reservation(
        user_id=uid,
        space_id=space.id,
        date=res_date,
        start_time=res_time,
        duration_hrs=duration,
        persons=persons,
        total_price=data.get("totalPrice", "Free"),
        payment_method=payment_method,
        notes=data.get("notes", ""),
        status="pending"
    )
    db.session.add(res)
    db.session.commit()

    # Notify student: booking received, waiting for owner approval
    _send_email(
        student.email,
        f"Booking Request Received - {space.name}",
        _email_html("#0f1f3d", "Booking Request Sent! ⏳", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>Your booking request at <strong>{space.name}</strong> has been sent to the owner for approval.</p>
        <p><strong>No payment is needed yet.</strong> You'll receive payment instructions once the owner approves your booking.</p>
        {_reservation_details_html(res)}
        <p style="color:#666;">We'll notify you as soon as the owner responds.</p>""")
    )

    # Notify owner: new booking request
    if space.owner_email:
        _send_email(
            space.owner_email,
            f"New Booking Request - {space.name}",
            _email_html("#0f1f3d", "New Booking Request 📋", f"""
            <p>Hi <strong>{space.owner_name or 'Owner'}</strong>,</p>
            <p><strong>{student.first_name} {student.last_name}</strong> ({student.email}) has requested a booking at <strong>{space.name}</strong>.</p>
            {_reservation_details_html(res)}
            <p>Please log in to Studify to <strong>approve or reject</strong> this request.</p>""")
        )

    return jsonify(res.to_dict()), 201


# ─── Owner confirms reservation ──────────────────────────────────────────────
#
#  - Pay on arrival → status: confirmed, seats deducted now
#  - GCash / Maya   → status: awaiting_payment, seats deducted now,
#                     student is told to send payment

@res_bp.route("/<int:res_id>/confirm", methods=["PATCH"])
@require_auth
def confirm_reservation(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)
    space  = StudySpace.query.get(res.space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    if res.status != "pending":
        return jsonify({"error": f"Cannot confirm a reservation with status '{res.status}'"}), 400

    # ── Deduct seats now that owner has approved ─────────────────────────────
    persons = res.persons or 1
    if space.available < persons:
        return jsonify({"error": "Not enough seats available anymore"}), 400

    space.available -= persons
    if space.available == 0:   space.status = "full"
    elif space.available <= 3: space.status = "busy"

    socketio.emit("availability_update", {
        "spaceId": space.id,
        "available": space.available,
        "status": space.status
    })

    student = User.query.get(res.user_id)

    if res.payment_method == "on_arrival":
        # No payment step needed — confirmed immediately
        res.status = "confirmed"
        db.session.commit()

        _send_email(
            student.email,
            f"Reservation Confirmed - {space.name}",
            _email_html("#0f1f3d", "Reservation Confirmed! ✅", f"""
            <p>Hi <strong>{student.first_name}</strong>,</p>
            <p>Your reservation at <strong>{space.name}</strong> has been confirmed!</p>
            {_reservation_details_html(res)}
            <p style="color:#666;">Please arrive on time and pay at the venue. See you there!</p>""")
        )
        return jsonify({"message": "Confirmed. Student notified to pay on arrival."})

    else:
        # GCash or Maya — ask student to send payment
        res.status = "awaiting_payment"
        db.session.commit()

        method_label = "GCash" if res.payment_method == "gcash" else "Maya"
        pay_number   = space.gcash_number if res.payment_method == "gcash" else space.maya_number

        _send_email(
            student.email,
            f"Booking Approved — Please Send Payment - {space.name}",
            _email_html("#1a7a4a", "Booking Approved! Now Send Payment 💳", f"""
            <p>Hi <strong>{student.first_name}</strong>,</p>
            <p>Great news! Your booking at <strong>{space.name}</strong> has been approved.</p>
            {_reservation_details_html(res)}
            <div style="background:#e8f5e9;border-radius:12px;padding:1.2rem;margin:1.2rem 0;border-left:4px solid #1a7a4a;">
              <p style="margin:0;"><strong>Please send ₱{res.total_price} via {method_label} to:</strong></p>
              <p style="font-size:1.4rem;font-weight:bold;margin:0.5rem 0;">{pay_number or 'Contact the owner for details'}</p>
              <p style="margin:0;color:#555;">After sending, log in to Studify and upload your payment proof (reference number or screenshot).</p>
            </div>
            <p style="color:#d94f2b;"><strong>Important:</strong> Your booking is not finalized until payment is verified.</p>""")
        )
        return jsonify({"message": f"Approved. Student notified to pay via {method_label}."})


# ─── Owner rejects reservation ───────────────────────────────────────────────
#
#  Since no payment was collected, there is NO refund issue here.

@res_bp.route("/<int:res_id>/reject", methods=["PATCH"])
@require_auth
def reject_reservation(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)
    space  = StudySpace.query.get(res.space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    if res.status != "pending":
        return jsonify({"error": f"Cannot reject a reservation with status '{res.status}'"}), 400

    # Seats were never deducted, so nothing to restore
    res.status = "rejected"
    db.session.commit()

    student = User.query.get(res.user_id)
    _send_email(
        student.email,
        f"Booking Request Declined - {space.name}",
        _email_html("#d94f2b", "Booking Not Available 😔", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>Unfortunately, the owner of <strong>{space.name}</strong> was unable to accommodate your booking on <strong>{res.date}</strong>.</p>
        <p style="color:#666;">No payment was taken. Please try booking a different time or space on Studify.</p>
        <p style="color:#666;">We're sorry for the inconvenience.</p>""")
    )

    return jsonify({"message": "Rejected. Student notified. No refund needed."})


# ─── Student submits payment proof ───────────────────────────────────────────
#
#  Called after owner approves a GCash/Maya booking.
#  Student provides a reference number or screenshot URL.

@res_bp.route("/<int:res_id>/payment", methods=["PATCH"])
@require_auth
def submit_payment(res_id):
    uid  = int(get_jwt_identity())
    res  = Reservation.query.get_or_404(res_id)

    if res.user_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    if res.status != "awaiting_payment":
        return jsonify({"error": f"Payment not expected for status '{res.status}'"}), 400

    data  = request.get_json()
    proof = data.get("paymentProof", "").strip()
    if not proof:
        return jsonify({"error": "Payment proof (reference number or screenshot URL) is required"}), 400

    res.payment_proof = proof
    res.status        = "payment_review"
    db.session.commit()

    space = StudySpace.query.get(res.space_id)

    # Notify owner to verify the payment
    if space.owner_email:
        student = User.query.get(uid)
        _send_email(
            space.owner_email,
            f"Payment Submitted for Review - {space.name}",
            _email_html("#0f1f3d", "Payment Proof Submitted 🧾", f"""
            <p>Hi <strong>{space.owner_name or 'Owner'}</strong>,</p>
            <p><strong>{student.first_name} {student.last_name}</strong> has submitted payment proof for their booking at <strong>{space.name}</strong>.</p>
            {_reservation_details_html(res)}
            <div style="background:white;border-radius:12px;padding:1.2rem;margin:1.2rem 0;">
              <p><strong>Payment Proof:</strong></p>
              <p style="word-break:break-all;">{proof}</p>
            </div>
            <p>Please log in to Studify to <strong>verify or reject</strong> the payment.</p>""")
        )

    return jsonify({"message": "Payment proof submitted. Awaiting owner verification."})


# ─── Owner verifies payment → fully confirmed ────────────────────────────────

@res_bp.route("/<int:res_id>/verify-payment", methods=["PATCH"])
@require_auth
def verify_payment(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)
    space  = StudySpace.query.get(res.space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    if res.status != "payment_review":
        return jsonify({"error": f"No payment to verify for status '{res.status}'"}), 400

    res.status = "confirmed"
    db.session.commit()

    student = User.query.get(res.user_id)
    _send_email(
        student.email,
        f"Payment Verified — You're All Set! - {space.name}",
        _email_html("#0f1f3d", "Payment Verified! ✅ See You There!", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>Your payment has been verified and your reservation at <strong>{space.name}</strong> is now fully confirmed!</p>
        {_reservation_details_html(res)}
        <p style="color:#666;">Please arrive on time. See you there!</p>""")
    )

    return jsonify({"message": "Payment verified. Reservation fully confirmed."})


# ─── Owner rejects payment proof ─────────────────────────────────────────────
#
#  If the proof is invalid, owner can bounce it back.
#  Student returns to "awaiting_payment" to resubmit.

@res_bp.route("/<int:res_id>/reject-payment", methods=["PATCH"])
@require_auth
def reject_payment(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)
    space  = StudySpace.query.get(res.space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    if res.status != "payment_review":
        return jsonify({"error": f"No payment proof to reject for status '{res.status}'"}), 400

    data   = request.get_json() or {}
    reason = data.get("reason", "The payment proof could not be verified.")

    res.status        = "awaiting_payment"
    res.payment_proof = None  # Clear the invalid proof
    db.session.commit()

    student = User.query.get(res.user_id)
    _send_email(
        student.email,
        f"Payment Proof Issue - {space.name}",
        _email_html("#d94f2b", "Payment Proof Not Accepted ⚠️", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>There was an issue with your payment proof for your booking at <strong>{space.name}</strong>.</p>
        <div style="background:white;border-radius:12px;padding:1.2rem;margin:1.2rem 0;border-left:4px solid #d94f2b;">
          <p><strong>Reason:</strong> {reason}</p>
        </div>
        <p>Please log in to Studify and resubmit a valid payment reference number or screenshot.</p>""")
    )

    return jsonify({"message": "Payment proof rejected. Student asked to resubmit."})


# ─── Cancel a reservation ────────────────────────────────────────────────────
#
#  Seats are only restored if the booking was already approved
#  (i.e. status was confirmed / awaiting_payment / payment_review).

@res_bp.route("/<int:res_id>/cancel", methods=["PATCH"])
@require_auth
def cancel_reservation(res_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    res    = Reservation.query.get_or_404(res_id)

    if res.user_id != uid and claims.get("role") != "admin" and res.space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    if res.status in ("cancelled", "rejected"):
        return jsonify({"error": "Already cancelled or rejected"}), 400

    # Only restore seats if they were already deducted (post-approval statuses)
    seats_were_deducted = res.status in ("confirmed", "awaiting_payment", "payment_review")

    res.status = "cancelled"

    if seats_were_deducted:
        res.space.available += res.persons or 1
        if res.space.available > 0:
            res.space.status = "open"
        socketio.emit("availability_update", {
            "spaceId": res.space.id,
            "available": res.space.available,
            "status": res.space.status
        })

    db.session.commit()

    student = User.query.get(res.user_id)

    # Build a refund notice if student had already paid
    refund_note = ""
    if seats_were_deducted and res.payment_method != "on_arrival" and res.payment_proof:
        refund_note = "<p style='color:#d94f2b;'><strong>Refund Notice:</strong> Since you had already submitted payment, please contact the space owner directly to arrange your refund.</p>"

    _send_email(
        student.email,
        f"Reservation Cancelled - {res.space.name}",
        _email_html("#d94f2b", "Reservation Cancelled", f"""
        <p>Hi <strong>{student.first_name}</strong>,</p>
        <p>Your reservation at <strong>{res.space.name}</strong> on <strong>{res.date}</strong> has been cancelled.</p>
        {refund_note}
        <p style="color:#666;">You can make a new booking anytime on Studify.</p>""")
    )

    return jsonify({"message": "Cancelled"})


# ─── Owner analytics ──────────────────────────────────────────────────────────

@res_bp.route("/analytics/<int:space_id>", methods=["GET"])
@require_auth
def analytics(space_id):
    uid    = int(get_jwt_identity())
    claims = get_jwt()
    space  = StudySpace.query.get_or_404(space_id)

    if claims.get("role") != "admin" and space.owner_id != uid:
        return jsonify({"error": "Not authorized"}), 403

    reservations = Reservation.query.filter_by(space_id=space_id).all()

    # Revenue: only confirmed bookings
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

    # Bookings by hour (exclude cancelled/rejected)
    bookings_by_hour = {}
    for r in reservations:
        if r.status in ("cancelled", "rejected"):
            continue
        hour = r.start_time.hour
        bookings_by_hour[hour] = bookings_by_hour.get(hour, 0) + 1

    # Status breakdown
    status_breakdown = {}
    for r in reservations:
        status_breakdown[r.status] = status_breakdown.get(r.status, 0) + 1

    total_revenue = sum(revenue_by_day.values())
    confirmed     = [r for r in reservations if r.status == "confirmed"]
    avg_duration  = (
        round(sum(r.duration_hrs for r in confirmed) / len(confirmed), 1)
        if confirmed else 0
    )

    return jsonify({
        "revenue_by_day":     [{"date": d, "revenue": round(v, 2)} for d, v in sorted(revenue_by_day.items())],
        "bookings_by_hour":   [{"hour": h, "count": c} for h, c in sorted(bookings_by_hour.items())],
        "status_breakdown":   status_breakdown,
        "total_revenue":      round(total_revenue, 2),
        "avg_duration":       avg_duration,
        "total_bookings":     len(reservations),
        "confirmed_bookings": len(confirmed)
    })