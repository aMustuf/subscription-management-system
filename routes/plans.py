"""Recurring plans. The admin decides, plan by plan, what customers may do."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func

from extensions import db
from models import BILLING_PERIODS, Plan, Subscription
from utils import Form, admin_required, staff_required

bp = Blueprint("plans", __name__, url_prefix="/plans")


@bp.route("/")
@staff_required
def index():
    plans = Plan.query.order_by(Plan.active.desc(), Plan.name).all()
    counts = dict(
        db.session.query(Subscription.plan_id, func.count(Subscription.id))
        .filter(Subscription.status.in_(["active", "paused"]))
        .group_by(Subscription.plan_id)
        .all()
    )
    return render_template("plans/list.html", plans=plans, counts=counts)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:plan_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(plan_id=None):
    plan = db.session.get(Plan, plan_id) if plan_id else None
    if plan_id and plan is None:
        abort(404)
    if plan:
        initial = {key: getattr(plan, key) for key in (
            "name", "price", "billing_period", "min_quantity", "start_date", "end_date", "auto_close",
            "closable", "pausable", "renewable", "description", "active")}
    else:
        initial = {"billing_period": "monthly", "min_quantity": 1, "closable": True, "renewable": True,
                   "active": True, "price": "0.00"}
    form = Form(request.form if request.method == "POST" else None, initial=initial)
    if form.submitted:
        data = {
            "name": form.text("name", "Name", max_len=120),
            "price": form.decimal("price", "Price", min_value=0, max_value=10000000),
            "billing_period": form.choice("billing_period", "Billing period", BILLING_PERIODS),
            "min_quantity": form.integer("min_quantity", "Minimum quantity", min_value=1, max_value=100000),
            "start_date": form.date("start_date", "Available from", required=False),
            "end_date": form.date("end_date", "Available until", required=False),
            "auto_close": form.boolean("auto_close"),
            "closable": form.boolean("closable"),
            "pausable": form.boolean("pausable"),
            "renewable": form.boolean("renewable"),
            "description": form.text("description", "Description", required=False, max_len=1000),
            "active": form.boolean("active"),
        }
        if data["start_date"] and data["end_date"] and data["end_date"] < data["start_date"]:
            form.error("end_date", "Available until must be on or after Available from.")
        if data["name"]:
            clash = Plan.query.filter(func.lower(Plan.name) == data["name"].lower())
            if plan:
                clash = clash.filter(Plan.id != plan.id)
            if clash.first():
                form.error("name", "A plan with this name already exists.")
        if form.ok:
            if plan is None:
                plan = Plan()
                db.session.add(plan)
            for key, value in data.items():
                setattr(plan, key, value)
            db.session.commit()
            flash(f"Plan {plan.name} saved.", "success")
            return redirect(url_for("plans.index"))
    return render_template("plans/form.html", form=form, plan=plan)
