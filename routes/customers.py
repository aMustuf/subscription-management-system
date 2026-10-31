"""Customers: the internal team creates and looks after customer accounts."""
from __future__ import annotations

import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from extensions import db
from models import Invoice, Subscription, User
from services.mailer import send_email
from utils import Form, current_page, make_token, staff_required

bp = Blueprint("customers", __name__, url_prefix="/customers")


def _get_customer(customer_id) -> User:
    customer = db.session.get(User, customer_id)
    if customer is None or customer.role != "customer":
        abort(404)
    return customer


@bp.route("/")
@staff_required
def index():
    q = request.args.get("q", "").strip()
    state = request.args.get("state", "")
    query = User.query.filter_by(role="customer")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.name.ilike(like), User.email.ilike(like), User.company.ilike(like)))
    if state == "active":
        query = query.filter(User.is_active_account.is_(True))
    elif state == "inactive":
        query = query.filter(User.is_active_account.is_(False))
    pagination = query.order_by(func.coalesce(User.company, User.name)).paginate(
        page=current_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False
    )
    ids = [c.id for c in pagination.items]
    active_subs = dict(
        db.session.query(Subscription.customer_id, func.count(Subscription.id))
        .filter(Subscription.customer_id.in_(ids), Subscription.status.in_(["active", "paused"]))
        .group_by(Subscription.customer_id)
        .all()
    )
    balances = dict(
        db.session.query(Invoice.customer_id, func.sum(Invoice.amount_total - Invoice.amount_paid))
        .filter(Invoice.customer_id.in_(ids), Invoice.status == "confirmed")
        .group_by(Invoice.customer_id)
        .all()
    )
    return render_template(
        "customers/list.html", pagination=pagination, q=q, state=state, active_subs=active_subs, balances=balances
    )


@bp.route("/new", methods=["GET", "POST"])
@staff_required
def create():
    form = Form(request.form if request.method == "POST" else None, initial={"send_invite": True})
    if form.submitted:
        data = _read_customer_form(form)
        if form.ok:
            customer = User(role="customer", **data)
            customer.set_password(secrets.token_urlsafe(24))  # replaced when they accept the invite
            db.session.add(customer)
            db.session.flush()
            if form.boolean("send_invite"):
                _send_invite(customer)
                flash(f"{customer.display_name} added. An invite to set a password was emailed to {customer.email}.",
                      "success")
            else:
                flash(f"{customer.display_name} added. They can use 'Forgot password' to log in later.", "success")
            db.session.commit()
            return redirect(url_for("customers.detail", customer_id=customer.id))
    return render_template("customers/form.html", form=form, customer=None)


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@staff_required
def edit(customer_id):
    customer = _get_customer(customer_id)
    form = Form(
        request.form if request.method == "POST" else None,
        initial={
            "name": customer.name,
            "email": customer.email,
            "company": customer.company,
            "phone": customer.phone,
            "address": customer.address,
            "gstin": customer.gstin,
            "active": customer.is_active_account,
        },
    )
    if form.submitted:
        data = _read_customer_form(form, existing=customer)
        if form.ok:
            for key, value in data.items():
                setattr(customer, key, value)
            customer.is_active_account = form.boolean("active")
            db.session.commit()
            flash("Customer saved.", "success")
            return redirect(url_for("customers.detail", customer_id=customer.id))
    return render_template("customers/form.html", form=form, customer=customer)


@bp.route("/<int:customer_id>")
@staff_required
def detail(customer_id):
    customer = _get_customer(customer_id)
    subscriptions = customer.subscriptions.order_by(Subscription.created_at.desc()).all()
    invoices = customer.invoices.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).limit(25).all()
    outstanding = sum((inv.amount_due for inv in customer.invoices.filter(Invoice.status == "confirmed")), start=0)
    return render_template(
        "customers/detail.html",
        customer=customer,
        subscriptions=subscriptions,
        invoices=invoices,
        outstanding=outstanding,
    )


@bp.route("/<int:customer_id>/invite", methods=["POST"])
@staff_required
def invite(customer_id):
    customer = _get_customer(customer_id)
    _send_invite(customer)
    db.session.commit()
    flash(f"Invite emailed to {customer.email}.", "success")
    return redirect(url_for("customers.detail", customer_id=customer.id))


def _read_customer_form(form: Form, existing: User | None = None) -> dict:
    data = {
        "name": form.text("name", "Contact name", max_len=120, min_len=2),
        "email": form.email("email"),
        "company": form.text("company", "Company", required=False, max_len=150),
        "phone": form.phone("phone"),
        "address": form.text("address", "Billing address", required=False, max_len=500),
        "gstin": form.text("gstin", "GSTIN", required=False, max_len=20),
    }
    email = data["email"]
    if email:
        clash = User.query.filter(User.email == email)
        if existing is not None:
            clash = clash.filter(User.id != existing.id)
        if clash.first():
            form.error("email", "Another account already uses this email.")
    return data


def _send_invite(customer: User) -> None:
    link = url_for("auth.accept_invite", token=make_token(customer, "invite"), _external=True)
    send_email(
        customer.email,
        "Your Brightloop customer portal is ready",
        f"Hi {customer.name},\n\nBrightloop Software set up a customer portal for "
        f"{customer.display_name}. Choose a password to see your subscriptions and invoices:\n\n{link}\n\n"
        "The link expires in 7 days.\n\n— Brightloop Software",
        kind="invite",
    )
