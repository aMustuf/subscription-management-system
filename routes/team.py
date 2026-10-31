"""Team: only the admin can add staff members (and other admins)."""
from __future__ import annotations

import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from extensions import db
from models import User
from services.mailer import send_email
from utils import Form, admin_required, make_token

bp = Blueprint("team", __name__, url_prefix="/team")

TEAM_ROLES = {"staff": "Staff (day-to-day work, limited access)", "admin": "Admin (full control)"}


@bp.route("/")
@admin_required
def index():
    members = User.query.filter(User.role.in_(["admin", "staff"])).order_by(User.role, User.name).all()
    return render_template("team/list.html", members=members)


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    form = Form(request.form if request.method == "POST" else None, initial={"role": "staff"})
    if form.submitted:
        name = form.text("name", "Full name", max_len=120, min_len=2)
        email = form.email("email")
        phone = form.phone("phone")
        role = form.choice("role", "Role", TEAM_ROLES)
        if email and User.query.filter_by(email=email).first():
            form.error("email", "Another account already uses this email.")
        if form.ok:
            member = User(name=name, email=email, phone=phone, role=role)
            member.set_password(secrets.token_urlsafe(24))  # they choose their own via the invite
            db.session.add(member)
            db.session.flush()
            link = url_for("auth.accept_invite", token=make_token(member, "invite"), _external=True)
            send_email(
                member.email,
                "You've been added to Brightloop",
                f"Hi {member.name},\n\n{current_user.name} added you to Brightloop as "
                f"{member.role_label.lower()}. Choose your password here:\n\n{link}\n\n"
                "The link expires in 7 days.",
                kind="invite",
            )
            db.session.commit()
            flash(f"{member.name} added as {member.role_label.lower()}. An invite was emailed to {member.email}.",
                  "success")
            return redirect(url_for("team.index"))
    return render_template("team/form.html", form=form, roles=TEAM_ROLES)


@bp.route("/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle(user_id):
    member = db.session.get(User, user_id)
    if member is None or member.role not in ("admin", "staff"):
        abort(404)
    if member.id == current_user.id:
        flash("You can't deactivate your own account.", "warning")
        return redirect(url_for("team.index"))
    member.is_active_account = not member.is_active_account
    db.session.commit()
    flash(f"{member.name} is now {'active' if member.is_active_account else 'deactivated and cannot log in'}.",
          "success")
    return redirect(url_for("team.index"))
