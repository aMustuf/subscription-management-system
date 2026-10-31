"""Payments received, with filters and totals."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import PAYMENT_METHODS, Invoice, Payment, User, money
from services.billing import delete_payment
from utils import admin_required, current_page, staff_required

bp = Blueprint("payments", __name__, url_prefix="/payments")


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


@bp.route("/")
@staff_required
def index():
    q = request.args.get("q", "").strip()
    method = request.args.get("method", "")
    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    query = Payment.query.join(Invoice, Payment.invoice_id == Invoice.id).join(User, Invoice.customer_id == User.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Payment.number.ilike(like), Invoice.number.ilike(like), User.name.ilike(like),
                                 User.company.ilike(like), Payment.reference.ilike(like)))
    if method in PAYMENT_METHODS:
        query = query.filter(Payment.method == method)
    if date_from:
        query = query.filter(Payment.payment_date >= date_from)
    if date_to:
        query = query.filter(Payment.payment_date <= date_to)
    total = money(query.with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar())
    by_method = dict(query.with_entities(Payment.method, func.sum(Payment.amount)).group_by(Payment.method).all())
    pagination = (
        query.options(joinedload(Payment.invoice).joinedload(Invoice.customer))
        .order_by(Payment.payment_date.desc(), Payment.id.desc())
        .paginate(page=current_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)
    )
    return render_template("payments/list.html", pagination=pagination, q=q, method=method, total=total,
                           by_method=by_method, date_from=request.args.get("from", ""),
                           date_to=request.args.get("to", ""))


@bp.route("/<int:payment_id>/delete", methods=["POST"])
@admin_required
def delete(payment_id):
    payment = db.session.get(Payment, payment_id)
    if payment is None:
        abort(404)
    invoice_id, number = payment.invoice_id, payment.number
    delete_payment(payment)
    db.session.commit()
    flash(f"Payment {number} removed. The invoice balance was updated.", "success")
    return redirect(url_for("invoices.detail", invoice_id=invoice_id))
