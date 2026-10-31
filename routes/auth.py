"""Sign up, log in, log out, forgot/reset password, and the user's own profile."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import User
from services.mailer import email_enabled, send_email
from utils import PASSWORD_RULES, Form, home_endpoint, is_safe_next, make_token, read_token

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint(current_user)))

    form = Form(request.form if request.method == "POST" else None)
    if form.submitted:
        email = form.email("email")
        password = request.form.get("password", "")
        if not password:
            form.error("password", "Password is required.")
        if form.ok:
            user = User.query.filter_by(email=email).first()
            if user is None or not user.check_password(password):
                form.error("form", "Email or password is incorrect.")
            elif not user.is_active:
                form.error("form", "This account is deactivated. Contact your admin.")
            else:
                login_user(user, remember=form.boolean("remember"))
                user.last_login_at = datetime.now()
                db.session.commit()
                flash(f"Welcome back, {user.name.split()[0]}.", "success")
                next_url = request.args.get("next")
                return redirect(next_url if is_safe_next(next_url) else url_for(home_endpoint(user)))
    return render_template("auth/login.html", form=form)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint(current_user)))

    form = Form(request.form if request.method == "POST" else None)
    if form.submitted:
        name = form.text("name", "Full name", max_len=120, min_len=2)
        company = form.text("company", "Company", required=False, max_len=150)
        email = form.email("email")
        password = form.password("password")
        if password and request.form.get("confirm_password") != password:
            form.error("confirm_password", "Passwords don't match.")
        if email and User.query.filter_by(email=email).first():
            form.error("email", "An account with this email already exists. Log in or reset your password.")
        if form.ok:
            user = User(name=name, company=company, email=email, role="customer")
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:  # two sign-ups with the same email at the same moment
                db.session.rollback()
                form.error("email", "An account with this email already exists.")
            else:
                login_user(user)
                flash("Your account is ready. This is your customer portal.", "success")
                return redirect(url_for("portal.home"))
    return render_template("auth/signup.html", form=form, rules=PASSWORD_RULES)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You're logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = Form(request.form if request.method == "POST" else None)
    demo_link = None
    if form.submitted:
        email = form.email("email")
        if form.ok:
            user = User.query.filter_by(email=email).first()
            if user and user.is_active:
                link = url_for("auth.reset_password", token=make_token(user, "reset"), _external=True)
                send_email(
                    user.email,
                    "Reset your Brightloop password",
                    f"Hi {user.name},\n\nUse this link to choose a new password. "
                    f"It works once and expires in 1 hour:\n\n{link}\n\n"
                    "If you didn't ask for this, you can ignore this email.\n\n— Brightloop Software",
                    kind="password_reset",
                )
                db.session.commit()
                if not email_enabled():
                    demo_link = link
            # Same message either way, so nobody can probe which emails have accounts
            flash("If an account exists for that email, a reset link is on its way.", "success")
            if not demo_link:
                return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html", form=form, demo_link=demo_link)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    return _set_password(token, "reset")


@bp.route("/set-password/<token>", methods=["GET", "POST"])
def accept_invite(token):
    return _set_password(token, "invite")


def _set_password(token, purpose):
    user = read_token(token, purpose)
    if user is None:
        flash("This link is invalid, already used or expired. Request a new reset link.", "danger")
        return redirect(url_for("auth.forgot_password"))

    form = Form(request.form if request.method == "POST" else None)
    if form.submitted:
        password = form.password("password")
        if password and request.form.get("confirm_password") != password:
            form.error("confirm_password", "Passwords don't match.")
        if form.ok:
            user.set_password(password)
            db.session.commit()
            flash("Password saved. Log in with your new password.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", form=form, user=user, purpose=purpose, rules=PASSWORD_RULES)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user
    action = request.form.get("action") if request.method == "POST" else None

    details = Form(
        request.form if action == "details" else None,
        initial={
            "name": user.name,
            "phone": user.phone,
            "company": user.company,
            "address": user.address,
            "gstin": user.gstin,
        },
    )
    if details.submitted:
        name = details.text("name", "Full name", max_len=120, min_len=2)
        phone = details.phone("phone")
        company = details.text("company", "Company", required=False, max_len=150)
        address = details.text("address", "Address", required=False, max_len=500)
        gstin = details.text("gstin", "GSTIN", required=False, max_len=20)
        if details.ok:
            user.name, user.phone, user.company, user.address, user.gstin = name, phone, company, address, gstin
            db.session.commit()
            flash("Profile saved.", "success")
            return redirect(url_for("auth.profile"))

    password_form = Form(request.form if action == "password" else None)
    if password_form.submitted:
        if not user.check_password(request.form.get("current_password", "")):
            password_form.error("current_password", "Current password is incorrect.")
        new_password = password_form.password("password", "New password")
        if new_password and request.form.get("confirm_password") != new_password:
            password_form.error("confirm_password", "Passwords don't match.")
        if password_form.ok:
            user.set_password(new_password)
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("auth.profile"))

    return render_template(
        "auth/profile.html",
        details=details,
        password_form=password_form,
        rules=PASSWORD_RULES,
        mail_on=bool(current_app.config.get("MAIL_SERVER")),
    )
