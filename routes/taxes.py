"""Tax rules (admin only). Lines use the product's tax, or the default tax."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func

from extensions import db
from models import Product, Tax
from utils import Form, admin_required

bp = Blueprint("taxes", __name__, url_prefix="/taxes")


@bp.route("/")
@admin_required
def index():
    taxes = Tax.query.order_by(Tax.active.desc(), Tax.rate.desc()).all()
    usage = dict(db.session.query(Product.tax_id, func.count(Product.id)).group_by(Product.tax_id).all())
    return render_template("taxes/list.html", taxes=taxes, usage=usage)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:tax_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(tax_id=None):
    tax = db.session.get(Tax, tax_id) if tax_id else None
    if tax_id and tax is None:
        abort(404)
    initial = {"active": True}
    if tax:
        initial = {"name": tax.name, "rate": tax.rate, "description": tax.description,
                   "is_default": tax.is_default, "active": tax.active}
    form = Form(request.form if request.method == "POST" else None, initial=initial)
    if form.submitted:
        name = form.text("name", "Name", max_len=60)
        rate = form.decimal("rate", "Rate", min_value=0, max_value=100)
        description = form.text("description", "Description", required=False, max_len=255)
        is_default = form.boolean("is_default")
        active = form.boolean("active")
        if name:
            clash = Tax.query.filter(func.lower(Tax.name) == name.lower())
            if tax:
                clash = clash.filter(Tax.id != tax.id)
            if clash.first():
                form.error("name", "A tax with this name already exists.")
        if is_default and not active:
            form.error("active", "The default tax must be active.")
        if tax and tax.is_default and not is_default:
            form.error("is_default", "Make another tax the default instead; one tax must stay the default.")
        if form.ok:
            if is_default:  # only one default tax at a time
                Tax.query.filter(Tax.is_default.is_(True)).update({"is_default": False})
            if tax is None:
                tax = Tax()
                db.session.add(tax)
            tax.name, tax.rate, tax.description, tax.is_default, tax.active = name, rate, description, is_default, active
            db.session.commit()
            flash(f"{tax.name} saved.", "success")
            return redirect(url_for("taxes.index"))
    return render_template("taxes/form.html", form=form, tax=tax)
