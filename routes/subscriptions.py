"""Subscriptions: quotes that become recurring contracts."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    LIFECYCLE,
    PAYMENT_TERMS,
    SUBSCRIPTION_STATUSES,
    Invoice,
    Plan,
    QuotationTemplate,
    Subscription,
    SubscriptionLine,
    Tax,
    User,
    next_number,
)
from services.catalog import (
    catalog_items,
    default_tax,
    item_key,
    read_lines,
    rows_from_lines,
    tax_for_product,
    unit_price_for,
)
from services.dates import DEFAULT_TERM, add_periods
from services.subscriptions import RuleError, apply_action, available_actions, check_rules, log, recompute_totals
from utils import Form, current_page, staff_required

bp = Blueprint("subscriptions", __name__, url_prefix="/subscriptions")

EDITABLE = ("draft", "quotation")


def _get(sub_id) -> Subscription:
    sub = db.session.get(Subscription, sub_id)
    if sub is None:
        abort(404)
    return sub


@bp.route("/")
@staff_required
def index():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    plan_id = request.args.get("plan", "")
    query = Subscription.query.join(User, Subscription.customer_id == User.id).options(
        joinedload(Subscription.customer), joinedload(Subscription.plan)
    )
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Subscription.number.ilike(like), User.name.ilike(like), User.company.ilike(like)))
    if plan_id.isdigit():
        query = query.filter(Subscription.plan_id == int(plan_id))
    counts_query = query.with_entities(Subscription.status, func.count(Subscription.id)).group_by(Subscription.status)
    counts = dict(counts_query.order_by(None).all())
    if status == "due":
        query = query.filter(Subscription.status == "active", Subscription.next_invoice_date <= date.today())
    elif status in SUBSCRIPTION_STATUSES:
        query = query.filter(Subscription.status == status)
    pagination = query.order_by(Subscription.id.desc()).paginate(
        page=current_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False
    )
    due_count = Subscription.query.filter(Subscription.status == "active",
                                          Subscription.next_invoice_date <= date.today()).count()
    plans = Plan.query.order_by(Plan.name).all()
    return render_template("subscriptions/list.html", pagination=pagination, q=q, status=status, plan_id=plan_id,
                           counts=counts, total=sum(counts.values()), due_count=due_count, plans=plans)


@bp.route("/new", methods=["GET", "POST"])
@staff_required
def create():
    template = None
    if request.values.get("template_id", "").isdigit():
        template = db.session.get(QuotationTemplate, int(request.values["template_id"]))
    today = date.today()
    initial = {"start_date": today, "payment_term_days": 15}
    rows = [{"item": "", "qty": "1", "price": "", "tax": "", "error": None}]
    if request.args.get("customer_id", "").isdigit():
        initial["customer_id"] = int(request.args["customer_id"])
    if template is not None:
        initial.update({
            "plan_id": template.plan_id,
            "notes": template.notes,
            "quotation_valid_until": today + timedelta(days=template.validity_days),
            "expiry_date": _default_expiry(today, template.plan),
        })
        rows = []
        for line in template.lines:
            tax = tax_for_product(line.product)
            rows.append({"item": item_key(line.product_id, line.variant_id), "qty": str(line.quantity),
                         "price": f"{unit_price_for(line.product, line.variant):f}", "tax": str(tax.id if tax else ""),
                         "error": None})
    else:
        initial["quotation_valid_until"] = today + timedelta(days=30)

    form = Form(request.form if request.method == "POST" else None, initial=initial)
    if form.submitted:
        sub = Subscription(created_by_id=current_user.id, status="draft", template=template)
        rows = _save_from_form(form, sub)
        if form.ok:
            sub.number = next_number("SUB")
            db.session.add(sub)
            log(sub, "Created" + (f" from template {template.name}" if template else ""), current_user,
                to_status="draft")
            db.session.commit()
            flash(f"Subscription {sub.number} created as a draft. Send the quotation or confirm it.", "success")
            return redirect(url_for("subscriptions.detail", sub_id=sub.id))
        db.session.rollback()
    return _render_form(form, rows, sub=None, template=template)


@bp.route("/<int:sub_id>/edit", methods=["GET", "POST"])
@staff_required
def edit(sub_id):
    sub = _get(sub_id)
    if sub.status not in EDITABLE:
        flash(f"A {sub.status_label.lower()} subscription can't be edited. Close it and create a new one if the "
              "deal changed.", "warning")
        return redirect(url_for("subscriptions.detail", sub_id=sub.id))
    initial = {key: getattr(sub, key) for key in (
        "customer_id", "plan_id", "start_date", "expiry_date", "payment_term_days", "quotation_valid_until", "notes")}
    rows = rows_from_lines(sub.lines, with_prices=True)
    form = Form(request.form if request.method == "POST" else None, initial=initial)
    if form.submitted:
        rows = _save_from_form(form, sub)
        if form.ok:
            log(sub, "Details and lines updated", current_user)
            db.session.commit()
            flash(f"{sub.number} saved.", "success")
            return redirect(url_for("subscriptions.detail", sub_id=sub.id))
        db.session.rollback()
        sub = _get(sub_id)
    return _render_form(form, rows, sub=sub, template=sub.template)


@bp.route("/<int:sub_id>")
@staff_required
def detail(sub_id):
    sub = _get(sub_id)
    invoices = sub.invoices.order_by(Invoice.period_start.desc(), Invoice.id.desc()).all()
    fee_tax = default_tax()
    return render_template(
        "subscriptions/detail.html",
        sub=sub,
        actions=available_actions(sub),
        problems=check_rules(sub) if sub.status in EDITABLE else [],
        invoices=invoices,
        lifecycle=LIFECYCLE,
        fee_tax=fee_tax,
        stage_dates=_stage_dates(sub),
    )


@bp.route("/<int:sub_id>/action/<action>", methods=["POST"])
@staff_required
def action(sub_id, action):
    sub = _get(sub_id)
    reason = (request.form.get("reason") or "").strip()[:255] or None
    try:
        message = apply_action(sub, action, user=current_user, reason=reason)
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        db.session.commit()
        flash(message, "success")
    return redirect(url_for("subscriptions.detail", sub_id=sub_id))


@bp.route("/<int:sub_id>/delete", methods=["POST"])
@staff_required
def delete(sub_id):
    sub = _get(sub_id)
    if sub.status != "draft" or sub.invoices.count():
        flash("Only drafts without invoices can be deleted. Close the subscription instead.", "warning")
        return redirect(url_for("subscriptions.detail", sub_id=sub.id))
    number = sub.number
    db.session.delete(sub)
    db.session.commit()
    flash(f"Draft {number} deleted.", "success")
    return redirect(url_for("subscriptions.index"))


# ---------------------------------------------------------------------------
def _default_expiry(start: date, plan: Plan | None):
    if plan is None:
        return None
    return add_periods(start, plan.billing_period, DEFAULT_TERM[plan.billing_period]) - timedelta(days=1)


def _save_from_form(form: Form, sub: Subscription) -> list[dict]:
    """Validate the form and copy it onto `sub`. Returns editor rows for re-rendering."""
    customer = form.record("customer_id", "Customer", User, where=lambda u: u.role == "customer" and u.is_active)
    plan = form.record("plan_id", "Plan", Plan, where=lambda p: p.active)
    start = form.date("start_date", "Start date")
    expiry = form.date("expiry_date", "Expiry date", required=False)
    terms = form.choice("payment_term_days", "Payment terms", PAYMENT_TERMS)
    valid_until = form.date("quotation_valid_until", "Quote valid until", required=False)
    notes = form.text("notes", "Notes", required=False, max_len=2000)
    lines, rows = read_lines(form, with_prices=True)

    if start and expiry and expiry < start:
        form.error("expiry_date", "Expiry date must be on or after the start date.")
    if plan and start and not plan.is_available_on(start):
        form.error("plan_id", f"{plan.name} is only available {plan.availability_label.lower()}.")
    if valid_until and valid_until < date.today():
        form.error("quotation_valid_until", "Quote valid until can't be in the past.")
    if plan and lines:
        total_qty = sum(line["quantity"] for line in lines)
        if total_qty < plan.min_quantity:
            form.error("lines", f"{plan.name} needs at least {plan.min_quantity} units in total; "
                                f"these lines have {total_qty}.")
    if not form.ok:
        return rows

    sub.customer, sub.plan, sub.start_date, sub.expiry_date = customer, plan, start, expiry
    sub.payment_term_days, sub.quotation_valid_until, sub.notes = terms, valid_until, notes
    sub.lines = [
        SubscriptionLine(product=line["product"], variant=line["variant"], quantity=line["quantity"],
                         unit_price=line["unit_price"], tax=line["tax"], enabled=True)
        for line in lines
    ]
    recompute_totals(sub)
    return rows


def _render_form(form, rows, sub, template):
    customers = User.query.filter_by(role="customer", is_active_account=True).order_by(
        func.coalesce(User.company, User.name)).all()
    plans = Plan.query.filter_by(active=True).order_by(Plan.name).all()
    templates = QuotationTemplate.query.filter_by(active=True).order_by(QuotationTemplate.name).all()
    taxes = Tax.query.filter_by(active=True).order_by(Tax.rate.desc()).all()
    fee_tax = default_tax()
    return render_template(
        "subscriptions/form.html",
        form=form,
        sub=sub,
        template=template,
        templates=templates,
        customers=customers,
        plans=plans,
        rows=rows,
        catalog=catalog_items(include_keys=[r["item"] for r in rows]),
        taxes=taxes,
        with_prices=True,
        default_tax_rate=fee_tax.rate if fee_tax else 0,
        default_terms=DEFAULT_TERM,
    )


def _stage_dates(sub: Subscription) -> dict:
    """When each lifecycle stage was reached (from the history log)."""
    reached = {"draft": sub.created_at}
    for entry in sorted(sub.logs, key=lambda e: e.created_at):
        if entry.to_status and entry.to_status not in reached:
            reached[entry.to_status] = entry.created_at
    return reached
