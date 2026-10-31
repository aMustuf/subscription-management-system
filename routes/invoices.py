"""Invoices: created from subscriptions, then confirmed, sent, printed, paid or cancelled."""
from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import INVOICE_STATUSES, PAYMENT_METHODS, Invoice, Subscription, User, money
from services.billing import (
    BillingError,
    bill_next_period,
    cancel_invoice,
    confirm_invoice,
    record_payment,
    send_invoice,
)
from utils import Form, current_page, staff_required

bp = Blueprint("invoices", __name__, url_prefix="/invoices")

FILTERS = {
    "": "All",
    "draft": "Draft",
    "outstanding": "Outstanding",
    "overdue": "Overdue",
    "paid": "Paid",
    "cancelled": "Cancelled",
}


def _get(invoice_id) -> Invoice:
    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None:
        abort(404)
    return invoice


def filtered_invoices(status: str, q: str = "", date_from=None, date_to=None):
    today = date.today()
    query = Invoice.query.join(User, Invoice.customer_id == User.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Invoice.number.ilike(like), User.name.ilike(like), User.company.ilike(like)))
    if date_from:
        query = query.filter(Invoice.invoice_date >= date_from)
    if date_to:
        query = query.filter(Invoice.invoice_date <= date_to)
    if status == "outstanding":
        query = query.filter(Invoice.status == "confirmed")
    elif status == "overdue":
        query = query.filter(Invoice.status == "confirmed", Invoice.due_date < today)
    elif status in INVOICE_STATUSES:
        query = query.filter(Invoice.status == status)
    return query


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


@bp.route("/")
@staff_required
def index():
    status = request.args.get("status", "")
    if status not in FILTERS:
        status = ""
    q = request.args.get("q", "").strip()
    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    query = filtered_invoices(status, q, date_from, date_to)
    summary = query.with_entities(
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.amount_total), 0),
        func.coalesce(func.sum(Invoice.amount_paid), 0),
    ).filter(Invoice.status != "cancelled").one()
    pagination = (
        query.options(joinedload(Invoice.customer), joinedload(Invoice.subscription))
        .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
        .paginate(page=current_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)
    )
    return render_template("invoices/list.html", pagination=pagination, status=status, q=q, filters=FILTERS,
                           date_from=request.args.get("from", ""), date_to=request.args.get("to", ""),
                           summary={"count": summary[0], "total": money(summary[1]), "paid": money(summary[2]),
                                    "due": money(summary[1]) - money(summary[2])})


@bp.route("/<int:invoice_id>")
@staff_required
def detail(invoice_id):
    invoice = _get(invoice_id)
    payment_form = Form(None, initial={"amount": invoice.amount_due, "payment_date": date.today(),
                                       "method": "bank_transfer"})
    return render_template("invoices/detail.html", invoice=invoice, payment_form=payment_form,
                           methods=PAYMENT_METHODS)


@bp.route("/<int:invoice_id>/print")
@staff_required
def print_view(invoice_id):
    return render_template("invoices/print.html", invoice=_get(invoice_id))


@bp.route("/from-subscription/<int:sub_id>", methods=["POST"])
@staff_required
def create_for_subscription(sub_id):
    sub = db.session.get(Subscription, sub_id)
    if sub is None:
        abort(404)
    early = sub.next_invoice_date is not None and sub.next_invoice_date > date.today()
    try:
        invoice = bill_next_period(sub, user=current_user, allow_early=True)
    except BillingError as exc:
        db.session.commit() if exc.moved_on else db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("subscriptions.detail", sub_id=sub_id))
    db.session.commit()
    note = " (billed in advance)" if early else ""
    flash(f"Invoice {invoice.number} created{note} with lines, discounts and taxes filled in. Review and confirm it.",
          "success")
    return redirect(url_for("invoices.detail", invoice_id=invoice.id))


@bp.route("/<int:invoice_id>/<action>", methods=["POST"])
@staff_required
def act(invoice_id, action):
    invoice = _get(invoice_id)
    try:
        if action == "confirm":
            confirm_invoice(invoice)
            message = f"{invoice.number} confirmed. It now counts as outstanding until paid."
        elif action == "cancel":
            message = cancel_invoice(invoice, current_user)
        elif action == "send":
            message = send_invoice(invoice)
        else:
            abort(404)
    except BillingError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        db.session.commit()
        flash(message, "success")
    return redirect(url_for("invoices.detail", invoice_id=invoice_id))


@bp.route("/<int:invoice_id>/payments", methods=["POST"])
@staff_required
def add_payment(invoice_id):
    invoice = _get(invoice_id)
    if invoice.status != "confirmed":
        reason = "Confirm the invoice before recording a payment." if invoice.status == "draft" else \
            f"{invoice.number} is {invoice.status_label.lower()}; it can't take payments."
        flash(reason, "warning")
        return redirect(url_for("invoices.detail", invoice_id=invoice.id))
    form = Form(request.form)
    amount = form.decimal("amount", "Amount", min_value="0.01", max_value=100000000)
    method = form.choice("method", "Method", PAYMENT_METHODS)
    paid_on = form.date("payment_date", "Payment date")
    reference = form.text("reference", "Reference", required=False, max_len=80)
    note = form.text("note", "Note", required=False, max_len=1000)
    if amount is not None and amount > invoice.amount_due:
        form.error("amount", f"Amount can't be more than the {invoice.amount_due} still due.")
    if paid_on and paid_on > date.today():
        form.error("payment_date", "Payment date can't be in the future.")
    if form.ok:
        try:
            payment = record_payment(invoice, amount, method, paid_on, reference, note, current_user)
        except BillingError as exc:
            db.session.rollback()
            form.error("form", str(exc))
        else:
            db.session.commit()
            state = "The invoice is now paid in full." if invoice.status == "paid" else \
                f"{invoice.amount_due} is still due."
            flash(f"Payment {payment.number} recorded. {state}", "success")
            return redirect(url_for("invoices.detail", invoice_id=invoice.id))
    return render_template("invoices/detail.html", invoice=invoice, payment_form=form, methods=PAYMENT_METHODS)
