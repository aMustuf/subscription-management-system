"""Tests for the rules in the problem statement. Run with:  python -m pytest -q"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from conftest import ADMIN, CUSTOMER, STAFF
from extensions import db
from models import Discount, EmailLog, Invoice, Plan, Product, Subscription, Tax, User
from services.billing import apply_discounts, run_billing
from services.dates import next_period_start, period_end
from utils import password_problems


def text(response):
    return response.data.decode()


# --- Accounts and roles ------------------------------------------------------
@pytest.mark.parametrize("password, ok", [
    ("Abcdefgh@", True),        # 9 characters with upper, lower and special
    ("Abcdef@1", False),        # only 8 characters: must be MORE than eight
    ("abcdefgh@", False),       # no uppercase
    ("ABCDEFGH@", False),       # no lowercase
    ("Abcdefghi", False),       # no special character
])
def test_password_rules(password, ok):
    assert (password_problems(password) == []) is ok


def test_signup_requires_unique_email(client):
    data = {"name": "Asha K", "email": "CUSTOMER@demo.com", "password": "Strong@Pass1", "confirm_password": "Strong@Pass1"}
    assert "already exists" in text(client.post("/signup", data=data))
    data["email"] = "asha@newshop.in"
    response = client.post("/signup", data=data, follow_redirects=True)
    assert "Your account is ready" in text(response)
    assert User.query.filter_by(email="asha@newshop.in").one().role == "customer"


def test_reset_link_works_once(client, app):
    client.post("/forgot-password", data={"email": CUSTOMER[0]})
    body = EmailLog.query.filter_by(kind="password_reset").one().body
    link = body[body.index("/reset-password/"):].split()[0]
    assert client.get(link).status_code == 200
    client.post(link, data={"password": "Brand@New123", "confirm_password": "Brand@New123"})
    assert "invalid, already used or expired" in text(client.get(link, follow_redirects=True))


def test_roles_limit_access(client, login):
    assert client.get("/dashboard").status_code == 302  # not logged in -> login page
    login(*CUSTOMER)
    for url in ["/dashboard", "/subscriptions/", "/invoices/", "/products/"]:
        assert client.get(url).status_code == 403
    login(*STAFF)
    assert client.get("/subscriptions/").status_code == 200
    for url in ["/team/", "/team/new", "/products/new", "/plans/new", "/discounts/new", "/taxes/"]:
        assert client.get(url).status_code == 403
    login(*ADMIN)
    assert client.get("/team/new").status_code == 200


def test_only_admin_can_add_staff(client, login):
    login(*STAFF)
    assert client.post("/team/new", data={"name": "X Y", "email": "x@b.com", "role": "staff"}).status_code == 403
    login(*ADMIN)
    client.post("/team/new", data={"name": "New Person", "email": "new@brightloop.com", "role": "staff"})
    assert User.query.filter_by(email="new@brightloop.com").one().role == "staff"


# --- Catalog -------------------------------------------------------------------
def test_odoo_brand_adds_560(app):
    crm = Product.query.filter_by(name="Brightloop CRM").one()
    odoo = next(v for v in crm.variants if v.value == "Odoo")
    assert odoo.price == Decimal("1760.00")


# --- Subscriptions -----------------------------------------------------------------
def _new_subscription(client, plan_name, qty, start=None):
    plan = Plan.query.filter_by(name=plan_name).one()
    crm = Product.query.filter_by(name="Brightloop CRM").one()
    customer = User.query.filter_by(email="vikram@northwindlogistics.in").one()
    tax = Tax.query.filter_by(is_default=True).one()
    return client.post("/subscriptions/new", data={
        "customer_id": customer.id, "plan_id": plan.id, "start_date": (start or date.today()).isoformat(),
        "payment_term_days": "15", "line_item": [f"{crm.id}:{crm.variants[0].id}"], "line_qty": [str(qty)],
        "line_price": ["1200"], "line_tax": [str(tax.id)],
    }, follow_redirects=True)


def test_plan_minimum_quantity_and_availability(client, login):
    login(*STAFF)
    assert "needs at least 5 units" in text(_new_subscription(client, "Yearly Business", 2))
    assert "only available" in text(_new_subscription(client, "Legacy Monthly 2025", 1))


def test_subscription_lifecycle(client, login):
    login(*STAFF)
    assert "created as a draft" in text(_new_subscription(client, "Monthly Standard", 3))
    sub = Subscription.query.order_by(Subscription.id.desc()).first()
    assert sub.number.startswith(f"SUB/{date.today().year}/")
    client.post(f"/subscriptions/{sub.id}/action/send_quote")
    assert db.session.get(Subscription, sub.id).status == "quotation"
    client.post(f"/subscriptions/{sub.id}/action/confirm")
    assert db.session.get(Subscription, sub.id).status == "active"  # starts today, so it goes live
    client.post(f"/subscriptions/{sub.id}/action/close", data={"reason": "Test"})
    sub = db.session.get(Subscription, sub.id)
    assert sub.status == "closed"
    assert [log.to_status for log in sorted(sub.logs, key=lambda e: e.id) if log.to_status] == \
        ["draft", "quotation", "confirmed", "active", "closed"]


def test_plan_rules_block_pause_and_early_close(client, login):
    login(*STAFF)
    sub = Subscription.query.join(Plan).filter(Plan.name == "Yearly Business", Subscription.status == "active").first()
    client.post(f"/subscriptions/{sub.id}/action/pause")
    client.post(f"/subscriptions/{sub.id}/action/close")
    assert db.session.get(Subscription, sub.id).status == "active"


def test_auto_close_at_expiry(client, login):
    login(*STAFF)  # the first request runs housekeeping
    sub = Subscription.query.join(User, Subscription.customer_id == User.id).filter(
        User.company == "Urban Bake House").one()
    assert sub.status == "closed"


# --- Billing ---------------------------------------------------------------------------
def test_billing_run_never_bills_twice(app):
    first = run_billing(date.today())
    assert first["count"] > 0
    assert run_billing(date.today())["count"] == 0
    duplicates = db.session.query(Invoice.subscription_id, Invoice.period_start, func.count()).filter(
        Invoice.status != "cancelled").group_by(Invoice.subscription_id, Invoice.period_start).having(
        func.count() > 1).all()
    assert duplicates == []
    due = Subscription.query.filter(Subscription.status == "active",
                                    Subscription.next_invoice_date <= date.today()).count()
    assert due == 0  # nothing skipped either


def test_database_rejects_duplicate_period(app):
    invoice = Invoice.query.filter(Invoice.subscription_id.isnot(None), Invoice.status != "cancelled").first()
    db.session.add(Invoice(number="DUP-1", subscription_id=invoice.subscription_id, customer_id=invoice.customer_id,
                           status="draft", invoice_date=date.today(), due_date=date.today(),
                           period_start=invoice.period_start))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_cancelling_latest_invoice_makes_period_billable_again(client, login):
    login(*STAFF)
    run_billing(date.today())
    invoice = Invoice.query.filter_by(status="draft").order_by(Invoice.id.desc()).first()
    sub_id, period = invoice.subscription_id, invoice.period_start
    client.post(f"/invoices/{invoice.id}/cancel")
    assert db.session.get(Subscription, sub_id).next_invoice_date == period
    client.post(f"/invoices/from-subscription/{sub_id}")
    again = Invoice.query.filter_by(subscription_id=sub_id, period_start=period, status="draft").one()
    assert again.id != invoice.id


def test_discounts_apply_automatically_and_stop_at_limit(app):
    result = run_billing(date.today())
    names = {d.name for inv in result["invoices"] for d in inv.applied_discounts}
    assert {"Bulk seats 10%", "Festive ₹500 off CRM"} <= names
    discount = Discount.query.filter_by(name="Launch week 20%").one()  # already used up in the demo data
    assert discount.state() == "used_up"
    assert not any(d.name == "Launch week 20%" for inv in result["invoices"] for d in inv.applied_discounts)


def test_percentage_discount_is_spread_over_lines():
    drafts = [
        {"product_id": 1, "quantity": 10, "amount": Decimal("10000.00"), "discount": Decimal("0.00")},
        {"product_id": 2, "quantity": 2, "amount": Decimal("500.00"), "discount": Decimal("0.00")},
    ]
    rule = Discount(name="T", discount_type="percent", value=Decimal("10"), min_quantity=10, applies_to="subscription",
                    start_date=date.today(), end_date=date.today(), times_used=0)
    applied = apply_discounts(drafts, date.today(), [rule])
    assert applied[0][1] == Decimal("1050.00")
    assert [d["discount"] for d in drafts] == [Decimal("1000.00"), Decimal("50.00")]
    assert rule.times_used == 1


def test_invoice_totals_and_tax(app):
    run_billing(date.today())
    for invoice in Invoice.query.all():
        lines = invoice.lines
        assert invoice.amount_total == sum((line.total for line in lines), Decimal("0"))
        assert invoice.amount_tax == sum((line.tax_amount for line in lines), Decimal("0"))
        for line in lines:
            expected = ((line.amount - line.discount_amount) * line.tax_rate / 100).quantize(Decimal("0.01"))
            assert abs(line.tax_amount - expected) <= Decimal("0.01")


def test_payments(client, login):
    login(*STAFF)
    invoice = Invoice.query.filter(Invoice.status == "confirmed").first()
    due = invoice.amount_due
    today = date.today().isoformat()
    client.post(f"/invoices/{invoice.id}/payments", data={"amount": str(due + 1), "method": "upi", "payment_date": today})
    assert db.session.get(Invoice, invoice.id).amount_paid == invoice.amount_paid  # overpayment refused
    client.post(f"/invoices/{invoice.id}/payments", data={"amount": "100", "method": "upi", "payment_date": today})
    assert db.session.get(Invoice, invoice.id).status == "confirmed"
    rest = db.session.get(Invoice, invoice.id).amount_due
    client.post(f"/invoices/{invoice.id}/payments", data={"amount": str(rest), "method": "cash", "payment_date": today,
                                                          "note": "Settled"})
    assert db.session.get(Invoice, invoice.id).status == "paid"


# --- Portal and reports -------------------------------------------------------------------
def test_customer_sees_only_own_records(client, login):
    login(*CUSTOMER)
    me = User.query.filter_by(email=CUSTOMER[0]).one()
    other_sub = Subscription.query.filter(Subscription.customer_id != me.id, Subscription.status != "draft").first()
    other_invoice = Invoice.query.filter(Invoice.customer_id != me.id).first()
    assert client.get(f"/portal/subscriptions/{other_sub.id}").status_code == 404
    assert client.get(f"/portal/invoices/{other_invoice.id}").status_code == 404
    assert client.post(f"/portal/invoices/{other_invoice.id}/pay").status_code == 404
    mine = Invoice.query.filter_by(customer_id=me.id, status="confirmed").first()
    client.post(f"/portal/invoices/{mine.id}/pay")
    assert db.session.get(Invoice, mine.id).status == "paid"


def test_dashboard_reports_and_exports_load(client, login):
    login(*ADMIN)
    for url in ["/dashboard", "/dashboard?period=last_12", "/reports/", "/reports/months.csv", "/invoices/export.csv",
                "/billing/", "/payments/", "/outbox/"]:
        assert client.get(url).status_code == 200, url


def test_monthly_periods_do_not_drift():
    start = date(2026, 1, 31)
    day, seen = start, []
    for _ in range(4):
        seen.append(day)
        day = next_period_start(start, day, "monthly")
    assert seen == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert period_end(date(2026, 2, 28), "monthly", anchor=start) == date(2026, 3, 30)
    assert next_period_start(start, date(2026, 3, 1) - timedelta(days=1), "daily") == date(2026, 3, 1)
