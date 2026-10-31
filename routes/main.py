"""Finance dashboard: active subscriptions, revenue, payments and overdue invoices in one place."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request
from sqlalchemy import case, func
from sqlalchemy.orm import joinedload

from extensions import db
from models import LIFECYCLE, SUBSCRIPTION_STATUSES, Invoice, Payment, Plan, Subscription, money
from services.dates import add_months
from utils import staff_required

bp = Blueprint("main", __name__)

PERIODS = {
    "this_month": "This month",
    "last_month": "Last month",
    "this_quarter": "This quarter",
    "this_year": "This year",
    "last_12": "Last 12 months",
    "custom": "Custom dates",
}


def resolve_period(key: str, raw_from: str | None, raw_to: str | None, today: date):
    month_start = today.replace(day=1)
    if key == "last_month":
        start = add_months(month_start, -1)
        return start, month_start - timedelta(days=1)
    if key == "this_quarter":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
    if key == "this_year":
        return date(today.year, 1, 1), today
    if key == "last_12":
        return add_months(month_start, -11), today
    if key == "custom":
        try:
            start = datetime.strptime(raw_from or "", "%Y-%m-%d").date()
            end = datetime.strptime(raw_to or "", "%Y-%m-%d").date()
            if start <= end:
                return start, end
        except ValueError:
            pass
    return month_start, today


# How many of each billing period make one month (for monthly recurring revenue)
MONTHLY_FACTOR = case(
    (Plan.billing_period == "daily", 30.0),
    (Plan.billing_period == "weekly", 52.0 / 12),
    (Plan.billing_period == "yearly", 1.0 / 12),
    else_=1.0,
)


@bp.route("/dashboard")
@staff_required
def dashboard():
    today = date.today()
    period = request.args.get("period", "this_month")
    if period not in PERIODS:
        period = "this_month"
    start, end = resolve_period(period, request.args.get("from"), request.args.get("to"), today)
    plan_id = request.args.get("plan", "")
    plan = db.session.get(Plan, int(plan_id)) if plan_id.isdigit() else None

    def by_plan(query, sub_column=Subscription.plan_id):
        return query.filter(sub_column == plan.id) if plan else query

    # Subscriptions by stage
    stage_counts = dict(
        by_plan(db.session.query(Subscription.status, func.count(Subscription.id))).group_by(Subscription.status).all()
    )
    stages = [(s, SUBSCRIPTION_STATUSES[s], stage_counts.get(s, 0)) for s in LIFECYCLE[:4] + ["paused", "closed"]]

    # Monthly recurring revenue (before tax) from active subscriptions
    mrr = by_plan(
        db.session.query(func.coalesce(func.sum(Subscription.amount_untaxed * MONTHLY_FACTOR), 0))
        .join(Plan, Subscription.plan_id == Plan.id)
        .filter(Subscription.status == "active")
    ).scalar()

    # Money in the selected period
    payments_q = by_plan(
        db.session.query(Payment)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .outerjoin(Subscription, Invoice.subscription_id == Subscription.id)
        .filter(Payment.payment_date >= start, Payment.payment_date <= end)
    )
    collected = payments_q.with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar()
    payment_count = payments_q.with_entities(func.count(Payment.id)).scalar()
    invoiced = by_plan(
        db.session.query(func.coalesce(func.sum(Invoice.amount_total), 0))
        .outerjoin(Subscription, Invoice.subscription_id == Subscription.id)
        .filter(Invoice.status.in_(["confirmed", "paid"]), Invoice.invoice_date >= start, Invoice.invoice_date <= end)
    ).scalar()

    # What is still owed today
    open_q = by_plan(
        db.session.query(Invoice)
        .outerjoin(Subscription, Invoice.subscription_id == Subscription.id)
        .filter(Invoice.status == "confirmed")
    )
    outstanding = open_q.with_entities(func.coalesce(func.sum(Invoice.amount_total - Invoice.amount_paid), 0)).scalar()
    overdue_q = open_q.filter(Invoice.due_date < today)
    overdue_total = overdue_q.with_entities(func.coalesce(func.sum(Invoice.amount_total - Invoice.amount_paid), 0)).scalar()
    overdue_count = overdue_q.with_entities(func.count(Invoice.id)).scalar()
    overdue_list = overdue_q.options(joinedload(Invoice.customer)).order_by(Invoice.due_date).limit(8).all()
    drafts = by_plan(
        db.session.query(func.count(Invoice.id)).outerjoin(Subscription, Invoice.subscription_id == Subscription.id)
        .filter(Invoice.status == "draft")
    ).scalar()

    # Revenue collected per month: the months in the range, at least the last 6
    last_month = date(end.year, end.month, 1)
    first_month = min(date(start.year, start.month, 1), add_months(last_month, -5))
    if (last_month.year - first_month.year) * 12 + last_month.month - first_month.month > 23:
        first_month = add_months(last_month, -23)
    month_rows = dict(
        by_plan(
            db.session.query(func.strftime("%Y-%m", Payment.payment_date), func.sum(Payment.amount))
            .join(Invoice, Payment.invoice_id == Invoice.id)
            .outerjoin(Subscription, Invoice.subscription_id == Subscription.id)
            .filter(Payment.payment_date >= first_month, Payment.payment_date <= end)
        ).group_by(func.strftime("%Y-%m", Payment.payment_date)).all()
    )
    months, cursor = [], first_month
    while cursor <= last_month:
        key = cursor.strftime("%Y-%m")
        # Axis label: month, with the year under the first month and every January
        tick = [cursor.strftime("%b"), str(cursor.year)] if (not months or cursor.month == 1) else cursor.strftime("%b")
        months.append({"label": cursor.strftime("%b %Y"), "tick": tick, "value": float(money(month_rows.get(key, 0)))})
        cursor = add_months(cursor, 1)

    # Recurring revenue per plan
    plan_rows = (
        db.session.query(Plan.name, func.count(Subscription.id), func.sum(Subscription.amount_untaxed * MONTHLY_FACTOR))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .filter(Subscription.status == "active")
        .group_by(Plan.id)
        .order_by(func.sum(Subscription.amount_untaxed * MONTHLY_FACTOR).desc())
        .all()
    )
    expiring = by_plan(
        Subscription.query.options(joinedload(Subscription.customer), joinedload(Subscription.plan)).filter(
            Subscription.status.in_(["active", "paused"]),
            Subscription.expiry_date >= today,
            Subscription.expiry_date <= today + timedelta(days=45),
        )
    ).order_by(Subscription.expiry_date).limit(6).all()
    recent_payments = (
        payments_q.options(joinedload(Payment.invoice).joinedload(Invoice.customer))
        .order_by(Payment.payment_date.desc(), Payment.id.desc())
        .limit(6)
        .all()
    )

    return render_template(
        "dashboard.html",
        periods=PERIODS,
        period=period,
        start=start,
        end=end,
        plans=Plan.query.order_by(Plan.name).all(),
        plan=plan,
        stages=stages,
        active=stage_counts.get("active", 0),
        paused=stage_counts.get("paused", 0),
        mrr=money(mrr),
        collected=money(collected),
        payment_count=payment_count,
        invoiced=money(invoiced),
        outstanding=money(outstanding),
        overdue_total=money(overdue_total),
        overdue_count=overdue_count,
        overdue_list=overdue_list,
        drafts=drafts,
        months=months,
        plan_rows=[(name, count, money(value or 0)) for name, count, value in plan_rows],
        expiring=expiring,
        recent_payments=recent_payments,
        raw_from=request.args.get("from", start.isoformat()),
        raw_to=request.args.get("to", end.isoformat()),
    )
