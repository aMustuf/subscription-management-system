"""Billing: invoices from subscriptions, automatic discounts and taxes, payments.

Guarantees from the brief:
  * Nothing is billed twice: one invoice per subscription period (checked here
    and enforced by a unique database index that ignores cancelled invoices).
  * Nothing is skipped: each invoice moves the subscription's next billing date
    forward by exactly one period, and a billing run catches up every period
    that is due. Cancelling the latest invoice makes its period billable again.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import current_app, url_for
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, selectinload

from extensions import db
from models import (
    PAYMENT_METHODS,
    ZERO,
    Discount,
    Invoice,
    InvoiceDiscount,
    InvoiceLine,
    Payment,
    Sequence,
    Subscription,
    SubscriptionLog,
    money,
    next_number,
)
from services.catalog import default_tax
from services.dates import next_period_start, period_end
from services.subscriptions import run_housekeeping


class BillingError(Exception):
    """A billing rule stopped the action; the message is shown to the user."""

    def __init__(self, message, moved_on=False):
        super().__init__(message)
        self.moved_on = moved_on  # True when the billing date was moved past an already-billed period


# ---------------------------------------------------------------------------
# Discounts
# ---------------------------------------------------------------------------
def running_discounts(today: date) -> list[Discount]:
    return (
        Discount.query.filter(
            Discount.active.is_(True),
            Discount.start_date <= today,
            Discount.end_date >= today,
            or_(Discount.usage_limit.is_(None), Discount.times_used < Discount.usage_limit),
        )
        .options(selectinload(Discount.products))
        .order_by(Discount.id)
        .all()
    )


def apply_discounts(drafts: list[dict], today: date, discounts=None) -> list[tuple[Discount, Decimal]]:
    """Apply every running discount whose rules match. Mutates each draft line's 'discount'."""
    applied = []
    for discount in (running_discounts(today) if discounts is None else discounts):
        if discount.usage_limit is not None and discount.times_used >= discount.usage_limit:
            continue  # used up earlier in this same billing run
        if discount.applies_to == "products":
            product_ids = {p.id for p in discount.products}
            eligible = [d for d in drafts if d["product_id"] in product_ids]
        else:
            eligible = list(drafts)
        if not eligible:
            continue
        purchase = sum((d["amount"] for d in eligible), ZERO)
        units = sum(d["quantity"] for d in eligible if d["product_id"] is not None)
        if discount.min_purchase and purchase < money(discount.min_purchase):
            continue
        if discount.min_quantity and units < discount.min_quantity:
            continue
        remaining_base = sum((d["amount"] - d["discount"] for d in eligible), ZERO)
        if remaining_base <= 0:
            continue
        if discount.discount_type == "percent":
            wanted = money(remaining_base * money(discount.value) / Decimal(100))
        else:
            wanted = min(money(discount.value), remaining_base)
        if wanted <= 0:
            continue
        # Spread the discount over the eligible lines in proportion to their amounts
        given, left = ZERO, wanted
        for index, d in enumerate(eligible):
            line_base = d["amount"] - d["discount"]
            share = left if index == len(eligible) - 1 else money(wanted * line_base / remaining_base)
            share = min(share, line_base, left)
            d["discount"] += share
            given += share
            left -= share
        applied.append((discount, money(given)))
        discount.times_used += 1
    return applied


# ---------------------------------------------------------------------------
# Creating invoices
# ---------------------------------------------------------------------------
class BillingContext:
    """Things every invoice in one billing run needs, loaded once (keeps big runs fast)."""

    def __init__(self, today: date, subscription: Subscription | None = None):
        self.today = today
        self.fee_tax = default_tax()
        self.discounts = running_discounts(today)
        self.sequence = Sequence.query.filter_by(name="INV", year=today.year).first()
        if self.sequence is None:
            self.sequence = Sequence(name="INV", year=today.year, value=0)
            db.session.add(self.sequence)
        billed = db.session.query(Invoice.subscription_id, Invoice.period_start).filter(Invoice.status != "cancelled")
        if subscription is not None:
            billed = billed.filter(Invoice.subscription_id == subscription.id)
        self.billed = set(billed.all())  # (subscription id, period start) pairs already invoiced

    def next_invoice_number(self) -> str:
        self.sequence.value += 1
        return f"INV/{self.sequence.year}/{self.sequence.value:05d}"


def build_invoice(sub: Subscription, period_start: date, today: date, ctx: BillingContext | None = None) -> Invoice:
    """Create an invoice for one billing period, with customer details, lines, discounts and taxes."""
    ctx = ctx or BillingContext(today, sub)
    plan = sub.plan
    end = period_end(period_start, plan.billing_period, anchor=sub.start_date)
    if sub.expiry_date and end > sub.expiry_date:
        end = sub.expiry_date
    customer = sub.customer
    invoice = Invoice(
        number=ctx.next_invoice_number(),
        subscription=sub,
        customer=customer,
        status="draft",
        invoice_date=today,
        due_date=today + timedelta(days=sub.payment_term_days),
        period_start=period_start,
        period_end=end,
        bill_to_name=customer.name,
        bill_to_company=customer.company,
        bill_to_email=customer.email,
        bill_to_address=customer.address,
        bill_to_gstin=customer.gstin,
        notes=sub.notes,
    )

    drafts = []
    period_text = f"{period_start:%d %b %Y} to {end:%d %b %Y}"
    if money(plan.price) > 0:
        drafts.append({"product_id": None, "description": f"{plan.name} plan fee ({period_text})", "quantity": 1,
                       "unit_price": money(plan.price), "tax": ctx.fee_tax})
    for line in sub.billable_lines:
        drafts.append({"product_id": line.product_id, "description": line.label, "quantity": line.quantity,
                       "unit_price": money(line.unit_price), "tax": line.tax})
    for d in drafts:
        d["amount"] = money(d["unit_price"] * d["quantity"])
        d["discount"] = ZERO

    applied = apply_discounts(drafts, today, ctx.discounts)

    totals = {"untaxed": ZERO, "discount": ZERO, "tax": ZERO, "total": ZERO}
    for d in drafts:
        taxable = d["amount"] - d["discount"]
        tax_amount = d["tax"].compute(taxable) if d["tax"] else ZERO
        invoice.lines.append(InvoiceLine(
            product_id=d["product_id"],
            description=d["description"],
            quantity=d["quantity"],
            unit_price=d["unit_price"],
            discount_amount=d["discount"],
            tax_name=d["tax"].name if d["tax"] else None,
            tax_rate=d["tax"].rate if d["tax"] else 0,
            amount=d["amount"],
            tax_amount=tax_amount,
            total=taxable + tax_amount,
        ))
        totals["untaxed"] += d["amount"]
        totals["discount"] += d["discount"]
        totals["tax"] += tax_amount
        totals["total"] += taxable + tax_amount
    for discount, amount in applied:
        invoice.applied_discounts.append(InvoiceDiscount(discount=discount, name=discount.name, amount=amount))
    invoice.amount_untaxed = money(totals["untaxed"])
    invoice.amount_discount = money(totals["discount"])
    invoice.amount_tax = money(totals["tax"])
    invoice.amount_total = money(totals["total"])
    invoice.amount_paid = ZERO
    return invoice


def bill_next_period(sub: Subscription, today: date | None = None, user=None, allow_early=False,
                     ctx: BillingContext | None = None) -> Invoice:
    """Invoice the subscription's next unbilled period and move its billing date forward."""
    today = today or date.today()
    if sub.status != "active":
        raise BillingError(f"Only active subscriptions are billed; {sub.number} is {sub.status_label.lower()}.")
    period_start = sub.next_invoice_date or sub.start_date
    if sub.expiry_date and period_start > sub.expiry_date:
        raise BillingError(f"{sub.number} has been billed up to its expiry date. Renew it to keep billing.")
    if period_start > today and not allow_early:
        raise BillingError(f"{sub.number} isn't due until {period_start:%d %b %Y}.")

    single = ctx is None
    ctx = ctx or BillingContext(today, sub)
    if (sub.id, period_start) in ctx.billed:
        sub.next_invoice_date = next_period_start(sub.start_date, period_start, sub.plan.billing_period)
        raise BillingError(f"The period from {period_start:%d %b %Y} is already invoiced; skipped.", moved_on=True)

    invoice = build_invoice(sub, period_start, today, ctx)
    db.session.add(invoice)
    ctx.billed.add((sub.id, period_start))
    sub.next_invoice_date = next_period_start(sub.start_date, period_start, sub.plan.billing_period)
    db.session.add(SubscriptionLog(
        subscription_id=sub.id, user_id=getattr(user, "id", None),
        message=f"Invoice {invoice.number} created for {invoice.period_start:%d %b} to {invoice.period_end:%d %b %Y}",
    ))
    if single:
        db.session.flush()  # the unique index (subscription, period) is the database's own guard
    return invoice


def run_billing(run_date: date | None = None, user=None, auto_confirm=False, max_periods_per_subscription=60) -> dict:
    """Billing day: invoice every active subscription that is due, catching up missed periods."""
    run_date = run_date or date.today()
    run_housekeeping(run_date)
    due = (
        Subscription.query.filter(Subscription.status == "active", Subscription.next_invoice_date <= run_date)
        .options(joinedload(Subscription.plan), joinedload(Subscription.customer), selectinload(Subscription.lines))
        .order_by(Subscription.next_invoice_date, Subscription.id)
        .all()
    )
    result = {"run_date": run_date, "subscriptions": len(due), "invoices": [], "skipped": [], "total": ZERO,
              "count": 0, "discounted": 0}
    ctx = BillingContext(run_date)
    with db.session.no_autoflush:  # write everything in one go at the end
        for sub in due:
            for _ in range(max_periods_per_subscription):
                if not (sub.next_invoice_date and sub.next_invoice_date <= run_date):
                    break
                if sub.expiry_date and sub.next_invoice_date > sub.expiry_date:
                    break
                try:
                    invoice = bill_next_period(sub, run_date, user, ctx=ctx)
                except BillingError as exc:
                    result["skipped"].append((sub.number, str(exc)))
                    if exc.moved_on:
                        continue
                    break
                if auto_confirm:
                    confirm_invoice(invoice)
                result["count"] += 1
                result["total"] += money(invoice.amount_total)
                result["discounted"] += 1 if invoice.applied_discounts else 0
                if len(result["invoices"]) < 50:  # shown on the results page
                    result["invoices"].append(invoice)
    db.session.commit()
    return result


def due_preview(run_date: date) -> dict:
    """How many subscriptions a billing run would invoice today."""
    query = Subscription.query.filter(Subscription.status == "active", Subscription.next_invoice_date <= run_date)
    subs = query.options(joinedload(Subscription.customer), joinedload(Subscription.plan)).order_by(
        Subscription.next_invoice_date).limit(200).all()
    return {"count": query.count(), "sample": subs}


# ---------------------------------------------------------------------------
# Invoice actions: confirm, cancel, send
# ---------------------------------------------------------------------------
def confirm_invoice(invoice: Invoice) -> None:
    if invoice.status != "draft":
        raise BillingError(f"Only draft invoices can be confirmed; {invoice.number} is {invoice.status_label.lower()}.")
    invoice.status = "confirmed"
    invoice.confirmed_at = datetime.now()


def cancel_invoice(invoice: Invoice, user=None) -> str:
    if invoice.status not in ("draft", "confirmed"):
        raise BillingError(f"A {invoice.status_label.lower()} invoice can't be cancelled.")
    if money(invoice.amount_paid) > 0:
        raise BillingError("This invoice has payments. Remove the payments before cancelling it.")
    invoice.status = "cancelled"
    invoice.cancelled_at = datetime.now()
    for applied in invoice.applied_discounts:  # give the discount use back
        if applied.discount is not None and applied.discount.times_used > 0:
            applied.discount.times_used -= 1
    message = f"{invoice.number} cancelled."
    sub = invoice.subscription
    if sub is not None and invoice.period_start is not None:
        if sub.next_invoice_date == next_period_start(sub.start_date, invoice.period_start, sub.plan.billing_period):
            sub.next_invoice_date = invoice.period_start  # so the period is not skipped
            message += f" The period from {invoice.period_start:%d %b %Y} can be billed again."
        db.session.add(SubscriptionLog(subscription_id=sub.id, user_id=getattr(user, "id", None),
                                       message=f"Invoice {invoice.number} cancelled"))
    return message


def send_invoice(invoice: Invoice) -> str:
    from services.mailer import send_email
    from utils import format_money

    if invoice.status == "cancelled":
        raise BillingError("Cancelled invoices can't be sent.")
    if invoice.status == "draft":
        confirm_invoice(invoice)
    link = url_for("portal.invoice", invoice_id=invoice.id, _external=True) \
        if "portal.invoice" in current_app.view_functions else url_for("auth.login", _external=True)
    lines = "\n".join(
        f"  {line.quantity} x {line.description}: {format_money(line.amount - line.discount_amount)}"
        for line in invoice.lines
    )
    discount = f"Discounts: -{format_money(invoice.amount_discount)}\n" if money(invoice.amount_discount) else ""
    send_email(
        invoice.bill_to_email,
        f"Invoice {invoice.number} from Brightloop Software",
        f"Hi {invoice.bill_to_name},\n\nYour invoice {invoice.number} for "
        f"{invoice.period_start:%d %b %Y} to {invoice.period_end:%d %b %Y}:\n\n{lines}\n\n{discount}"
        f"Tax: {format_money(invoice.amount_tax)}\nTotal: {format_money(invoice.amount_total)}\n"
        f"Amount due: {format_money(invoice.amount_due)} by {invoice.due_date:%d %b %Y}\n\n"
        f"View, print or pay it here:\n{link}\n\n— Brightloop Software",
        kind="invoice",
    )
    invoice.sent_at = datetime.now()
    return f"{invoice.number} emailed to {invoice.bill_to_email}."


def send_reminder(invoice: Invoice) -> str:
    """Email the customer about an unpaid invoice (used to chase overdue ones)."""
    from services.mailer import send_email
    from utils import format_money

    if invoice.status != "confirmed" or invoice.amount_due <= 0:
        raise BillingError(f"{invoice.number} has nothing left to pay.")
    link = url_for("portal.invoice", invoice_id=invoice.id, _external=True) \
        if "portal.invoice" in current_app.view_functions else url_for("auth.login", _external=True)
    late = (date.today() - invoice.due_date).days
    when = f"was due on {invoice.due_date:%d %b %Y} ({late} days ago)" if late > 0 else \
        f"is due on {invoice.due_date:%d %b %Y}"
    send_email(
        invoice.bill_to_email,
        f"Reminder: invoice {invoice.number} {'is overdue' if late > 0 else 'is due soon'}",
        f"Hi {invoice.bill_to_name},\n\nA friendly reminder that invoice {invoice.number} for "
        f"{format_money(invoice.amount_total)} {when}. Amount still due: {format_money(invoice.amount_due)}.\n\n"
        f"Pay online or download it here:\n{link}\n\n"
        "If you've already paid, thank you, and please ignore this email.\n\n— Brightloop Software",
        kind="reminder",
    )
    return f"Reminder for {invoice.number} emailed to {invoice.bill_to_email}."


def overdue_invoices(today: date | None = None):
    today = today or date.today()
    return Invoice.query.filter(Invoice.status == "confirmed", Invoice.due_date < today,
                                Invoice.amount_total > Invoice.amount_paid)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def record_payment(invoice: Invoice, amount: Decimal, method: str, payment_date: date, reference=None,
                   note=None, user=None) -> Payment:
    if invoice.status == "draft":
        raise BillingError("Confirm the invoice before recording a payment.")
    if invoice.status in ("paid", "cancelled"):
        raise BillingError(f"{invoice.number} is {invoice.status_label.lower()}; it can't take payments.")
    amount = money(amount)
    if amount <= 0:
        raise BillingError("Amount must be more than zero.")
    if amount > invoice.amount_due:
        raise BillingError(f"Amount can't be more than the {invoice.amount_due} still due.")
    if method not in PAYMENT_METHODS:
        raise BillingError("Choose a valid payment method.")
    if payment_date > date.today():
        raise BillingError("Payment date can't be in the future.")
    payment = Payment(number=next_number("PAY"), invoice=invoice, amount=amount, method=method,
                      payment_date=payment_date, reference=reference, note=note,
                      recorded_by_id=getattr(user, "id", None))
    db.session.add(payment)
    invoice.amount_paid = money(invoice.amount_paid) + amount
    if invoice.amount_due <= 0:
        invoice.status = "paid"
        invoice.paid_at = datetime.now()
    return payment


def delete_payment(payment: Payment) -> None:
    invoice = payment.invoice
    invoice.amount_paid = max(money(invoice.amount_paid) - money(payment.amount), ZERO)
    if invoice.status == "paid" and invoice.amount_due > 0:
        invoice.status = "confirmed"
        invoice.paid_at = None
    db.session.delete(payment)
