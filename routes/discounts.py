"""Discount rules. Only the admin can create or change them; staff can see them."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from extensions import db
from models import DISCOUNT_SCOPES, DISCOUNT_TYPES, Discount, Product
from utils import Form, admin_required, staff_required

bp = Blueprint("discounts", __name__, url_prefix="/discounts")

STATE_LABELS = {
    "running": "Running",
    "scheduled": "Scheduled",
    "expired": "Expired",
    "used_up": "Used up",
    "disabled": "Disabled",
}


@bp.route("/")
@staff_required
def index():
    discounts = Discount.query.order_by(Discount.end_date.desc()).all()
    return render_template("discounts/list.html", discounts=discounts, state_labels=STATE_LABELS)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:discount_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(discount_id=None):
    discount = db.session.get(Discount, discount_id) if discount_id else None
    if discount_id and discount is None:
        abort(404)
    if discount:
        initial = {key: getattr(discount, key) for key in (
            "name", "discount_type", "value", "min_purchase", "min_quantity", "start_date", "end_date",
            "usage_limit", "applies_to", "active")}
        initial["product_ids"] = [str(p.id) for p in discount.products]
    else:
        initial = {"discount_type": "percent", "applies_to": "subscription", "min_purchase": "0.00",
                   "min_quantity": 0, "active": True, "product_ids": []}
    form = Form(request.form if request.method == "POST" else None, initial=initial)
    if form.submitted:
        form.values["product_ids"] = request.form.getlist("product_ids")
        data = {
            "name": form.text("name", "Name", max_len=120),
            "discount_type": form.choice("discount_type", "Type", DISCOUNT_TYPES),
            "value": form.decimal("value", "Value", min_value="0.01", max_value=10000000),
            "min_purchase": form.decimal("min_purchase", "Minimum purchase", required=False, min_value=0,
                                         max_value=10000000, default=0),
            "min_quantity": form.integer("min_quantity", "Minimum quantity", required=False, min_value=0,
                                         max_value=100000, default=0),
            "start_date": form.date("start_date", "Start date"),
            "end_date": form.date("end_date", "End date"),
            "usage_limit": form.integer("usage_limit", "Usage limit", required=False, min_value=1,
                                        max_value=1000000),
            "applies_to": form.choice("applies_to", "Applies to", DISCOUNT_SCOPES),
            "active": form.boolean("active"),
        }
        if data["discount_type"] == "percent" and data["value"] is not None and data["value"] > 100:
            form.error("value", "A percentage discount can't be more than 100%.")
        if data["start_date"] and data["end_date"] and data["end_date"] < data["start_date"]:
            form.error("end_date", "End date must be on or after the start date.")
        products = []
        if data["applies_to"] == "products":
            ids = [int(i) for i in request.form.getlist("product_ids") if i.isdigit()]
            products = Product.query.filter(Product.id.in_(ids)).all() if ids else []
            if not products:
                form.error("product_ids", "Pick at least one product, or apply the discount to the whole subscription.")
        if form.ok:
            if discount is None:
                discount = Discount(created_by_id=current_user.id)
                db.session.add(discount)
            for key, value in data.items():
                setattr(discount, key, value)
            discount.products = products
            db.session.commit()
            flash(f"Discount {discount.name} saved. It applies on its own to the next matching invoices.", "success")
            return redirect(url_for("discounts.index"))
    products = Product.query.filter_by(active=True).order_by(Product.name).all()
    return render_template("discounts/form.html", form=form, discount=discount, products=products)
