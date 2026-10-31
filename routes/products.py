"""Products and their variants. Staff can look; only the admin can change them."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from extensions import db
from models import PRODUCT_TYPES, Product, ProductVariant, SubscriptionLine, Tax, TemplateLine
from utils import Form, admin_required, current_page, staff_required

bp = Blueprint("products", __name__, url_prefix="/products")


def _get(product_id) -> Product:
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    return product


def _tax_options():
    return [(t.id, f"{t.name} ({t.rate:g}%)") for t in Tax.query.filter_by(active=True).order_by(Tax.rate.desc())]


@bp.route("/")
@staff_required
def index():
    q = request.args.get("q", "").strip()
    ptype = request.args.get("type", "")
    show = request.args.get("show", "active")
    query = Product.query
    if q:
        query = query.filter(or_(Product.name.ilike(f"%{q}%"), Product.notes.ilike(f"%{q}%")))
    if ptype in PRODUCT_TYPES:
        query = query.filter(Product.product_type == ptype)
    if show == "active":
        query = query.filter(Product.active.is_(True))
    elif show == "archived":
        query = query.filter(Product.active.is_(False))
    pagination = query.order_by(Product.name).paginate(
        page=current_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False
    )
    return render_template("products/list.html", pagination=pagination, q=q, ptype=ptype, show=show)


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    form = Form(request.form if request.method == "POST" else None,
                initial={"product_type": "service", "active": True, "cost_price": "0.00"})
    if form.submitted:
        data = _read_product_form(form)
        if form.ok:
            product = Product(**data)
            db.session.add(product)
            db.session.commit()
            flash(f"{product.name} created. Add variants below if it comes in options.", "success")
            return redirect(url_for("products.detail", product_id=product.id))
    return render_template("products/form.html", form=form, product=None, taxes=_tax_options())


@bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(product_id):
    product = _get(product_id)
    form = Form(
        request.form if request.method == "POST" else None,
        initial={
            "name": product.name,
            "product_type": product.product_type,
            "sales_price": product.sales_price,
            "cost_price": product.cost_price,
            "tax_id": product.tax_id,
            "notes": product.notes,
            "active": product.active,
        },
    )
    if form.submitted:
        data = _read_product_form(form, existing=product)
        if form.ok:
            for key, value in data.items():
                setattr(product, key, value)
            db.session.commit()
            flash("Product saved.", "success")
            return redirect(url_for("products.detail", product_id=product.id))
    return render_template("products/form.html", form=form, product=product, taxes=_tax_options())


@bp.route("/<int:product_id>")
@staff_required
def detail(product_id):
    product = _get(product_id)
    variant_form = Form(None, initial={"extra_price": "0.00"})
    return render_template("products/detail.html", product=product, variant_form=variant_form)


@bp.route("/<int:product_id>/variants", methods=["POST"])
@admin_required
def add_variant(product_id):
    product = _get(product_id)
    variant_form = Form(request.form)
    attribute = variant_form.text("attribute", "Attribute", max_len=60)
    value = variant_form.text("value", "Value", max_len=80)
    extra = variant_form.decimal("extra_price", "Extra price", min_value=0, max_value=10000000)
    if attribute and value:
        exists = ProductVariant.query.filter(
            ProductVariant.product_id == product.id,
            func.lower(ProductVariant.attribute) == attribute.lower(),
            func.lower(ProductVariant.value) == value.lower(),
        ).first()
        if exists:
            variant_form.error("value", f"{attribute}: {value} already exists for this product.")
    if not variant_form.ok:
        return render_template("products/detail.html", product=product, variant_form=variant_form)
    db.session.add(ProductVariant(product=product, attribute=attribute, value=value, extra_price=extra))
    db.session.commit()
    flash(f"Variant {attribute}: {value} added.", "success")
    return redirect(url_for("products.detail", product_id=product.id))


@bp.route("/variants/<int:variant_id>/delete", methods=["POST"])
@admin_required
def delete_variant(variant_id):
    variant = db.session.get(ProductVariant, variant_id)
    if variant is None:
        abort(404)
    product_id = variant.product_id
    in_use = (
        SubscriptionLine.query.filter_by(variant_id=variant.id).first()
        or TemplateLine.query.filter_by(variant_id=variant.id).first()
    )
    if in_use:
        variant.active = not variant.active
        state = "available again" if variant.active else "hidden from new quotes (it's used on existing ones)"
        flash(f"{variant.label} is {state}.", "info")
    else:
        db.session.delete(variant)
        flash(f"Variant {variant.label} deleted.", "success")
    db.session.commit()
    return redirect(url_for("products.detail", product_id=product_id))


def _read_product_form(form: Form, existing: Product | None = None) -> dict:
    name = form.text("name", "Name", max_len=150)
    data = {
        "name": name,
        "product_type": form.choice("product_type", "Type", PRODUCT_TYPES),
        "sales_price": form.decimal("sales_price", "Sales price", min_value=0, max_value=10000000),
        "cost_price": form.decimal("cost_price", "Cost price", required=False, min_value=0, max_value=10000000,
                                   default=0),
        "notes": form.text("notes", "Notes", required=False, max_len=2000),
        "active": form.boolean("active"),
    }
    tax = form.record("tax_id", "Tax", Tax, required=False, where=lambda t: t.active)
    data["tax_id"] = tax.id if tax else None
    if name:
        clash = Product.query.filter(func.lower(Product.name) == name.lower())
        if existing is not None:
            clash = clash.filter(Product.id != existing.id)
        if clash.first():
            form.error("name", "A product with this name already exists.")
    return data
