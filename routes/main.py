"""Dashboard for the internal team (admin and staff)."""
from __future__ import annotations

from flask import Blueprint, render_template

from models import Plan, Product, Subscription, User
from utils import staff_required

bp = Blueprint("main", __name__)


@bp.route("/dashboard")
@staff_required
def dashboard():
    counts = {
        "customers": User.query.filter_by(role="customer").count(),
        "products": Product.query.filter_by(active=True).count(),
        "plans": Plan.query.filter_by(active=True).count(),
        "subscriptions": Subscription.query.filter_by(status="active").count(),
    }
    return render_template("dashboard.html", counts=counts)
