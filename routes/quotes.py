"""Quotation templates: ready-made offers with a plan, product lines and a validity period."""
from __future__ import annotations

from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from extensions import db
from models import Plan, QuotationTemplate, TemplateLine, money
from services.catalog import catalog_items, read_lines, rows_from_lines, unit_price_for
from utils import Form, staff_required

bp = Blueprint("quotes", __name__, url_prefix="/quotation-templates")


def template_estimate(template: QuotationTemplate) -> Decimal:
    """Price per billing period before tax, at today's catalog prices."""
    total = money(template.plan.price)
    for line in template.lines:
        total += unit_price_for(line.product, line.variant) * line.quantity
    return money(total)


@bp.route("/")
@staff_required
def index():
    templates = (
        QuotationTemplate.query.options(selectinload(QuotationTemplate.lines))
        .order_by(QuotationTemplate.active.desc(), QuotationTemplate.name)
        .all()
    )
    return render_template("quotes/list.html", templates=templates, estimate=template_estimate)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:template_id>/edit", methods=["GET", "POST"])
@staff_required
def edit(template_id=None):
    template = db.session.get(QuotationTemplate, template_id) if template_id else None
    if template_id and template is None:
        abort(404)
    initial = {"validity_days": 30, "active": True}
    rows = [{"item": "", "qty": "1", "error": None}]
    if template:
        initial = {"name": template.name, "validity_days": template.validity_days, "plan_id": template.plan_id,
                   "notes": template.notes, "active": template.active}
        rows = rows_from_lines(template.lines, with_prices=False)
    form = Form(request.form if request.method == "POST" else None, initial=initial)
    if form.submitted:
        name = form.text("name", "Name", max_len=120)
        validity = form.integer("validity_days", "Valid for (days)", min_value=1, max_value=365)
        plan = form.record("plan_id", "Plan", Plan, where=lambda p: p.active)
        notes = form.text("notes", "Notes", required=False, max_len=2000)
        lines, rows = read_lines(form, with_prices=False)
        if name:
            clash = QuotationTemplate.query.filter(func.lower(QuotationTemplate.name) == name.lower())
            if template:
                clash = clash.filter(QuotationTemplate.id != template.id)
            if clash.first():
                form.error("name", "A template with this name already exists.")
        if plan and lines:
            total_qty = sum(line["quantity"] for line in lines)
            if total_qty < plan.min_quantity:
                form.error("lines", f"{plan.name} needs at least {plan.min_quantity} units in total; "
                                    f"these lines have {total_qty}.")
        if form.ok:
            if template is None:
                template = QuotationTemplate()
                db.session.add(template)
            template.name, template.validity_days, template.plan, template.notes = name, validity, plan, notes
            template.active = form.boolean("active")
            template.lines = [
                TemplateLine(product=line["product"], variant=line["variant"], quantity=line["quantity"])
                for line in lines
            ]
            db.session.commit()
            flash(f"Template {template.name} saved.", "success")
            return redirect(url_for("quotes.index"))
    plans = Plan.query.filter_by(active=True).order_by(Plan.name).all()
    plan_options = [(p.id, f"{p.name} ({p.period_label}, min {p.min_quantity})") for p in plans]
    catalog = catalog_items(include_keys=[row["item"] for row in rows])
    return render_template("quotes/form.html", form=form, template=template, plan_options=plan_options,
                           rows=rows, catalog=catalog, with_prices=False, taxes=[])
