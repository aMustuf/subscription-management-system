"""Subscription rules: totals, status changes, and daily housekeeping."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from flask import current_app, url_for

from extensions import db
from models import ZERO, Subscription, SubscriptionLog, money
from services.catalog import default_tax
from services.dates import DEFAULT_TERM, add_periods

# Which buttons each status offers (the plan's rules can still block some)
ACTIONS = {
    "send_quote": "Send quotation",
    "confirm": "Confirm",
    "activate": "Start subscription",
    "pause": "Pause",
    "resume": "Resume",
    "renew": "Renew",
    "close": "Close",
}


class RuleError(Exception):
    """A business rule stopped the action; the message is shown to the user."""


def log(sub: Subscription, message: str, user=None, from_status=None, to_status=None) -> None:
    sub.logs.append(
        SubscriptionLog(message=message, user_id=getattr(user, "id", None), from_status=from_status,
                        to_status=to_status)
    )


def set_status(sub: Subscription, new_status: str, message: str, user=None) -> None:
    old = sub.status
    sub.status = new_status
    log(sub, message, user, from_status=old, to_status=new_status)


def recompute_totals(sub: Subscription) -> None:
    """Per-period amounts: plan fee (with the default tax) + enabled product lines."""
    fee = money(sub.plan.price) if sub.plan else ZERO
    tax_rule = default_tax()
    untaxed = fee
    tax = tax_rule.compute(fee) if (tax_rule and fee) else ZERO
    for line in sub.lines:
        if line.enabled is not False:  # new lines are enabled until saved
            untaxed += line.amount
            tax += line.tax_amount
    sub.amount_untaxed, sub.amount_tax, sub.amount_total = money(untaxed), money(tax), money(untaxed + tax)


def check_rules(sub: Subscription) -> list[str]:
    """Problems that stop a subscription from being confirmed."""
    problems = []
    plan = sub.plan
    if not sub.lines:
        problems.append("Add at least one product line.")
    if plan is not None:
        if not plan.active:
            problems.append(f"The plan {plan.name} is inactive.")
        elif not plan.is_available_on(sub.start_date):
            problems.append(f"{plan.name} is only available {plan.availability_label.lower()}; "
                            "pick another plan or start date.")
        total_qty = sum(line.quantity for line in sub.lines)
        if total_qty < plan.min_quantity:
            problems.append(f"{plan.name} needs at least {plan.min_quantity} units in total; "
                            f"this subscription has {total_qty}.")
    return problems


def can_close_now(sub: Subscription, today: date | None = None) -> tuple[bool, str]:
    today = today or date.today()
    if sub.status == "closed":
        return False, "Already closed."
    if sub.status in ("active", "paused") and not sub.plan.closable:
        if sub.expiry_date is None or sub.expiry_date >= today:
            return False, f"{sub.plan.name} can't be closed before its expiry date."
    return True, ""


def available_actions(sub: Subscription, today: date | None = None) -> list[dict]:
    """Buttons to show on the subscription page, with a reason when one is blocked."""
    today = today or date.today()
    plan = sub.plan
    actions = []

    def add(key, enabled=True, reason="", style="primary"):
        actions.append({"key": key, "label": ACTIONS[key], "enabled": enabled, "reason": reason, "style": style})

    if sub.status == "draft":
        add("send_quote")
        add("confirm", not sub.is_quotation_expired, "The quotation has expired. Edit it to set a new date.",
            style="outline")
    elif sub.status == "quotation":
        add("confirm", not sub.is_quotation_expired, "The quotation has expired. Edit it to set a new date.")
    elif sub.status == "confirmed":
        add("activate")
    elif sub.status == "active":
        if plan.pausable:
            add("pause", style="outline")
        if plan.renewable:
            add("renew", sub.expiry_date is not None, "Set an expiry date first.", style="outline")
    elif sub.status == "paused":
        add("resume")
        if plan.renewable:
            add("renew", sub.expiry_date is not None, "Set an expiry date first.", style="outline")
    if sub.status != "closed":
        ok, reason = can_close_now(sub, today)
        add("close", ok, reason, style="danger")
    return actions


def apply_action(sub: Subscription, action: str, user=None, reason: str | None = None,
                 today: date | None = None) -> str:
    """Run one lifecycle action. Returns a success message or raises RuleError."""
    today = today or date.today()
    plan = sub.plan
    allowed = {a["key"]: a for a in available_actions(sub, today)}
    if action not in allowed:
        raise RuleError(f"You can't {ACTIONS.get(action, action).lower()} a {sub.status_label.lower()} subscription.")
    if not allowed[action]["enabled"]:
        raise RuleError(allowed[action]["reason"])

    if action in ("send_quote", "confirm"):
        problems = check_rules(sub)
        if problems:
            raise RuleError(" ".join(problems))

    if action == "send_quote":
        if sub.quotation_valid_until is None or sub.quotation_valid_until < today:
            days = sub.template.validity_days if sub.template else 30
            sub.quotation_valid_until = today + timedelta(days=days)
        set_status(sub, "quotation", f"Quotation sent to {sub.customer.email}", user)
        _email_quotation(sub)
        return f"Quotation {sub.number} sent to {sub.customer.email}. It's valid until {sub.quotation_valid_until:%d %b %Y}."

    if action == "confirm":
        sub.confirmed_at = datetime.now()
        sub.next_invoice_date = sub.start_date
        set_status(sub, "confirmed", "Confirmed" + (f" by {user.name}" if user else ""), user)
        if sub.start_date <= today:
            _activate(sub, user, "Started (start date reached)")
            return f"{sub.number} confirmed and active. It's ready to bill."
        return f"{sub.number} confirmed. It becomes active on {sub.start_date:%d %b %Y}."

    if action == "activate":
        _activate(sub, user, "Started" + (f" by {user.name}" if user else ""))
        return f"{sub.number} is active. Invoices can now be created."

    if action == "pause":
        set_status(sub, "paused", "Paused: billing stops until resumed" + (f" ({reason})" if reason else ""), user)
        return f"{sub.number} paused. Billing runs will skip it."

    if action == "resume":
        if sub.next_invoice_date and sub.next_invoice_date < today:
            sub.next_invoice_date = today  # paused time is not billed
        set_status(sub, "active", "Resumed", user)
        return f"{sub.number} resumed. Next invoice on {sub.next_invoice_date:%d %b %Y}."

    if action == "renew":
        term = DEFAULT_TERM[plan.billing_period]  # one more standard term, e.g. 12 months
        old_expiry = sub.expiry_date
        sub.expiry_date = add_periods(old_expiry + timedelta(days=1), plan.billing_period, term) - timedelta(days=1)
        log(sub, f"Renewed: expiry moved from {old_expiry:%d %b %Y} to {sub.expiry_date:%d %b %Y}", user)
        return f"{sub.number} renewed until {sub.expiry_date:%d %b %Y}."

    if action == "close":
        sub.closed_at = datetime.now()
        sub.close_reason = reason
        set_status(sub, "closed", "Closed" + (f": {reason}" if reason else ""), user)
        return f"{sub.number} closed. No more invoices will be created for it."

    raise RuleError("Unknown action.")


def _activate(sub: Subscription, user, message: str) -> None:
    sub.activated_at = datetime.now()
    if sub.next_invoice_date is None:
        sub.next_invoice_date = sub.start_date
    set_status(sub, "active", message, user)


def _email_quotation(sub: Subscription) -> None:
    from services.mailer import send_email
    from utils import format_money

    link = url_for("portal.subscription", sub_id=sub.id, _external=True) \
        if "portal.subscription" in current_app.view_functions else url_for("auth.login", _external=True)
    lines = "\n".join(f"  {line.quantity} x {line.label}: {format_money(line.amount)}" for line in sub.lines)
    send_email(
        sub.customer.email,
        f"Quotation {sub.number} from Brightloop Software",
        f"Hi {sub.customer.name},\n\nHere is your quotation {sub.number} for the {sub.plan.name} plan "
        f"({sub.plan.period_label.lower()} billing).\n\n"
        f"  Plan fee: {format_money(sub.plan.price)}\n{lines}\n\n"
        f"Total per {sub.plan.period_unit}: {format_money(sub.amount_total)} including tax.\n"
        f"Valid until {sub.quotation_valid_until:%d %b %Y}.\n\n"
        f"Review and accept it in your portal:\n{link}\n\n— Brightloop Software",
        kind="quotation",
    )


# ---------------------------------------------------------------------------
# Housekeeping: start confirmed subscriptions on their start date and close
# expired ones on plans that close automatically.
# ---------------------------------------------------------------------------
def run_housekeeping(today: date | None = None) -> int:
    today = today or date.today()
    changed = 0
    for sub in Subscription.query.filter(Subscription.status == "confirmed",
                                         Subscription.start_date <= today).all():
        _activate(sub, None, "Started automatically on its start date")
        changed += 1
    expired = Subscription.query.filter(
        Subscription.status.in_(["active", "paused"]),
        Subscription.expiry_date.isnot(None),
        Subscription.expiry_date < today,
    ).all()
    for sub in expired:
        if sub.plan.auto_close:
            sub.closed_at = datetime.now()
            sub.close_reason = "Reached its expiry date"
            set_status(sub, "closed", f"Closed automatically: the {sub.plan.name} plan ends at expiry")
            changed += 1
    return changed


_last_housekeeping = {"at": -1e9}


def housekeeping_once_a_minute() -> None:
    now = time.monotonic()
    if now - _last_housekeeping["at"] < 60:
        return
    _last_housekeeping["at"] = now
    try:
        if run_housekeeping():
            db.session.commit()
    except Exception:  # noqa: BLE001 - never block a page because of housekeeping
        db.session.rollback()
        current_app.logger.exception("Housekeeping failed")
