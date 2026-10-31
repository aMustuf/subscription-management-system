"""Customer portal: customers see and manage only their own subscriptions and invoices."""
from __future__ import annotations

import secrets
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import LIFECYCLE, Invoice, Subscription, SubscriptionLine
from services.billing import BillingError, record_payment
from services.subscriptions import RuleError, apply_action, available_actions, log, recompute_totals
from utils import customer_required

bp = Blueprint("portal", __name__, url_prefix="/portal")

CUSTOMER_ACTIONS = {"confirm", "pause", "resume", "renew", "close"}


def _own_subscription(sub_id) -> Subscription:
    sub = db.session.get(Subscription, sub_id)
    # Drafts are internal; anything that isn't theirs is simply "not found"
    if sub is None or sub.customer_id != current_user.id or sub.status == "draft":
        abort(404)
    return sub


def _own_invoice(invoice_id) -> Invoice:
    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None or invoice.customer_id != current_user.id or invoice.status == "draft":
        abort(404)
    return invoice


def _visible_subscriptions():
    return Subscription.query.filter(Subscription.customer_id == current_user.id, Subscription.status != "draft")


def _visible_invoices():
    return Invoice.query.filter(Invoice.customer_id == current_user.id, Invoice.status != "draft")


@bp.route("/")
@customer_required
def home():
    subscriptions = _visible_subscriptions().order_by(Subscription.created_at.desc()).all()
    open_invoices = _visible_invoices().filter(Invoice.status == "confirmed").order_by(Invoice.due_date).all()
    amount_due = sum((inv.amount_due for inv in open_invoices), start=0)
    quotes = [s for s in subscriptions if s.status == "quotation"]
    live = [s for s in subscriptions if s.status in ("active", "paused")]
    next_dates = [s.next_invoice_date for s in live if s.status == "active" and s.next_invoice_date]
    recent = _visible_invoices().order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).limit(5).all()
    return render_template(
        "portal/home.html",
        subscriptions=subscriptions,
        quotes=quotes,
        live=live,
        open_invoices=open_invoices,
        amount_due=amount_due,
        next_invoice=min(next_dates) if next_dates else None,
        recent=recent,
    )


@bp.route("/subscriptions")
@customer_required
def subscriptions():
    subs = _visible_subscriptions().order_by(Subscription.created_at.desc()).all()
    return render_template("portal/subscriptions.html", subscriptions=subs)


@bp.route("/subscriptions/<int:sub_id>")
@customer_required
def subscription(sub_id):
    sub = _own_subscription(sub_id)
    actions = [a for a in available_actions(sub) if a["key"] in CUSTOMER_ACTIONS]
    invoices = sub.invoices.filter(Invoice.status != "draft").order_by(Invoice.period_start.desc()).all()
    return render_template("portal/subscription.html", sub=sub, actions=actions, invoices=invoices,
                           lifecycle=LIFECYCLE)


@bp.route("/subscriptions/<int:sub_id>/<action>", methods=["POST"])
@customer_required
def subscription_action(sub_id, action):
    sub = _own_subscription(sub_id)
    if action == "decline":
        action, reason = "close", "Quotation declined by the customer"
        if sub.status != "quotation":
            abort(404)
    else:
        reason = (request.form.get("reason") or "").strip()[:255] or None
    if action not in CUSTOMER_ACTIONS:
        abort(404)
    try:
        message = apply_action(sub, action, user=current_user, reason=reason)
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        db.session.commit()
        if action == "confirm":
            message = f"Thanks! Quotation {sub.number} is accepted."
        flash(message, "success")
    return redirect(url_for("portal.subscription", sub_id=sub.id))


@bp.route("/subscriptions/<int:sub_id>/lines/<int:line_id>/toggle", methods=["POST"])
@customer_required
def toggle_addon(sub_id, line_id):
    sub = _own_subscription(sub_id)
    line = db.session.get(SubscriptionLine, line_id)
    if line is None or line.subscription_id != sub.id or line.product.product_type != "addon":
        abort(404)
    if sub.status not in ("active", "paused", "confirmed", "quotation"):
        flash("Add-ons can't be changed on a closed subscription.", "warning")
        return redirect(url_for("portal.subscription", sub_id=sub.id))
    line.enabled = not line.enabled
    recompute_totals(sub)
    state = "on" if line.enabled else "off"
    log(sub, f"{current_user.name} switched {line.label} {state}", current_user)
    db.session.commit()
    flash(f"{line.label} switched {state}. Your next invoice will reflect it.", "success")
    return redirect(url_for("portal.subscription", sub_id=sub.id))


@bp.route("/invoices")
@customer_required
def invoices():
    show = request.args.get("show", "")
    query = _visible_invoices()
    if show == "open":
        query = query.filter(Invoice.status == "confirmed")
    elif show == "paid":
        query = query.filter(Invoice.status == "paid")
    items = query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all()
    totals = _visible_invoices().filter(Invoice.status == "confirmed").with_entities(
        func.coalesce(func.sum(Invoice.amount_total - Invoice.amount_paid), 0)).scalar()
    return render_template("portal/invoices.html", invoices=items, show=show, amount_due=totals)


@bp.route("/invoices/<int:invoice_id>")
@customer_required
def invoice(invoice_id):
    return render_template("portal/invoice.html", invoice=_own_invoice(invoice_id))


@bp.route("/invoices/<int:invoice_id>/print")
@customer_required
def print_invoice(invoice_id):
    return render_template("invoices/print.html", invoice=_own_invoice(invoice_id))


@bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
@customer_required
def pay(invoice_id):
    """Test payment in place of a real payment gateway."""
    invoice = _own_invoice(invoice_id)
    try:
        payment = record_payment(invoice, invoice.amount_due, "online", date.today(),
                                 reference=f"ONLINE-{secrets.token_hex(4).upper()}",
                                 note="Paid by the customer in the portal (test payment)", user=current_user)
    except BillingError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        db.session.commit()
        flash(f"Payment received, thank you. Receipt {payment.number}. {invoice.number} is now paid.", "success")
    return redirect(url_for("portal.invoice", invoice_id=invoice.id))
