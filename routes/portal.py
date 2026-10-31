"""Customer portal: customers see only their own subscriptions and invoices."""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user

from models import Invoice, Subscription
from utils import customer_required

bp = Blueprint("portal", __name__, url_prefix="/portal")


@bp.route("/")
@customer_required
def home():
    subscriptions = (
        Subscription.query.filter_by(customer_id=current_user.id)
        .filter(Subscription.status != "draft")
        .order_by(Subscription.created_at.desc())
        .all()
    )
    invoices = (
        Invoice.query.filter_by(customer_id=current_user.id)
        .filter(Invoice.status.in_(["confirmed", "paid"]))
        .order_by(Invoice.invoice_date.desc())
        .limit(10)
        .all()
    )
    return render_template("portal/home.html", subscriptions=subscriptions, invoices=invoices)
