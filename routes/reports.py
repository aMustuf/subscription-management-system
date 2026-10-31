"""Reports: revenue by month, by plan and by customer, with CSV downloads."""
from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, abort, render_template, request
from sqlalchemy import func

from extensions import db
from models import ZERO, Invoice, Payment, Plan, Subscription, User, money
from routes.main import MONTHLY_FACTOR
from services.dates import add_months
from services.exports import csv_response
from utils import staff_required

bp = Blueprint("reports", __name__, url_prefix="/reports")

BILLED = ("confirmed", "paid")


def _range():
    today = date.today()
    default_start = add_months(today.replace(day=1), -5)
    try:
        start = datetime.strptime(request.args.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        start = default_start
    try:
        end = datetime.strptime(request.args.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        end = today
    if end < start:
        start, end = end, start
    return start, end


def by_month(start: date, end: date) -> list[dict]:
    month = func.strftime("%Y-%m", Invoice.invoice_date)
    billed = {
        key: (count, money(total), money(discount), money(tax))
        for key, count, total, discount, tax in db.session.query(
            month, func.count(Invoice.id), func.sum(Invoice.amount_total), func.sum(Invoice.amount_discount),
            func.sum(Invoice.amount_tax)
        ).filter(Invoice.status.in_(BILLED), Invoice.invoice_date.between(start, end)).group_by(month)
    }
    pay_month = func.strftime("%Y-%m", Payment.payment_date)
    collected = {
        key: money(total) for key, total in db.session.query(pay_month, func.sum(Payment.amount))
        .filter(Payment.payment_date.between(start, end)).group_by(pay_month)
    }
    rows, cursor = [], start.replace(day=1)
    while cursor <= end:
        key = cursor.strftime("%Y-%m")
        count, total, discount, tax = billed.get(key, (0, ZERO, ZERO, ZERO))
        rows.append({"month": cursor.strftime("%b %Y"), "invoices": count, "invoiced": total, "discounts": discount,
                     "tax": tax, "collected": collected.get(key, ZERO)})
        cursor = add_months(cursor, 1)
    return rows


def by_plan(start: date, end: date) -> list[dict]:
    plans = Plan.query.order_by(Plan.name).all()
    active = dict(
        db.session.query(Subscription.plan_id, func.count(Subscription.id))
        .filter(Subscription.status == "active").group_by(Subscription.plan_id)
    )
    mrr = dict(
        db.session.query(Subscription.plan_id, func.sum(Subscription.amount_untaxed * MONTHLY_FACTOR))
        .join(Plan, Subscription.plan_id == Plan.id)
        .filter(Subscription.status == "active").group_by(Subscription.plan_id)
    )
    invoiced = dict(
        db.session.query(Subscription.plan_id, func.sum(Invoice.amount_total))
        .join(Invoice, Invoice.subscription_id == Subscription.id)
        .filter(Invoice.status.in_(BILLED), Invoice.invoice_date.between(start, end))
        .group_by(Subscription.plan_id)
    )
    collected = dict(
        db.session.query(Subscription.plan_id, func.sum(Payment.amount))
        .join(Invoice, Invoice.subscription_id == Subscription.id)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .filter(Payment.payment_date.between(start, end))
        .group_by(Subscription.plan_id)
    )
    return [
        {"plan": p.name, "period": p.period_label, "active": active.get(p.id, 0), "mrr": money(mrr.get(p.id) or 0),
         "invoiced": money(invoiced.get(p.id) or 0), "collected": money(collected.get(p.id) or 0)}
        for p in plans
    ]


def by_customer(start: date, end: date, limit: int = 50) -> list[dict]:
    invoiced = (
        db.session.query(Invoice.customer_id, func.sum(Invoice.amount_total).label("total"), func.count(Invoice.id))
        .filter(Invoice.status.in_(BILLED), Invoice.invoice_date.between(start, end))
        .group_by(Invoice.customer_id)
        .order_by(func.sum(Invoice.amount_total).desc())
        .limit(limit)
        .all()
    )
    ids = [row[0] for row in invoiced]
    customers = {u.id: u for u in User.query.filter(User.id.in_(ids))}
    collected = dict(
        db.session.query(Invoice.customer_id, func.sum(Payment.amount))
        .join(Payment, Payment.invoice_id == Invoice.id)
        .filter(Invoice.customer_id.in_(ids), Payment.payment_date.between(start, end))
        .group_by(Invoice.customer_id)
    )
    owed = dict(
        db.session.query(Invoice.customer_id, func.sum(Invoice.amount_total - Invoice.amount_paid))
        .filter(Invoice.customer_id.in_(ids), Invoice.status == "confirmed")
        .group_by(Invoice.customer_id)
    )
    return [
        {"customer": customers[cid].display_name, "email": customers[cid].email, "invoices": count,
         "invoiced": money(total), "collected": money(collected.get(cid) or 0), "owed": money(owed.get(cid) or 0)}
        for cid, total, count in invoiced
    ]


@bp.route("/")
@staff_required
def index():
    start, end = _range()
    months = by_month(start, end)
    totals = {key: sum((row[key] for row in months), ZERO) for key in ("invoiced", "discounts", "tax", "collected")}
    totals["invoices"] = sum(row["invoices"] for row in months)
    return render_template("reports/index.html", start=start, end=end, months=months, totals=totals,
                           plans=by_plan(start, end), customers=by_customer(start, end))


@bp.route("/<kind>.csv")
@staff_required
def export(kind):
    start, end = _range()
    stamp = f"{start:%Y-%m-%d}-to-{end:%Y-%m-%d}"
    if kind == "months":
        rows = by_month(start, end)
        return csv_response(f"revenue-by-month-{stamp}.csv",
                            ["Month", "Invoices", "Invoiced", "Discounts", "Tax", "Collected"],
                            [[r["month"], r["invoices"], r["invoiced"], r["discounts"], r["tax"], r["collected"]]
                             for r in rows])
    if kind == "plans":
        rows = by_plan(start, end)
        return csv_response(f"revenue-by-plan-{stamp}.csv",
                            ["Plan", "Billing", "Active subscriptions", "MRR", "Invoiced", "Collected"],
                            [[r["plan"], r["period"], r["active"], r["mrr"], r["invoiced"], r["collected"]]
                             for r in rows])
    if kind == "customers":
        rows = by_customer(start, end, limit=100000)
        return csv_response(f"revenue-by-customer-{stamp}.csv",
                            ["Customer", "Email", "Invoices", "Invoiced", "Collected", "Owed now"],
                            [[r["customer"], r["email"], r["invoices"], r["invoiced"], r["collected"], r["owed"]]
                             for r in rows])
    abort(404)
