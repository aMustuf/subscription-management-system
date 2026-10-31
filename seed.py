"""Demo data for Brightloop Software.

    python seed.py            wipe the database and load fresh demo data
    python seed.py --bulk 3000   also add 3,000 extra subscriptions (speed test)

All dates are relative to today, so the demo always looks current.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

from extensions import db
from models import (
    Discount,
    Plan,
    Product,
    ProductVariant,
    QuotationTemplate,
    Subscription,
    SubscriptionLine,
    Tax,
    TemplateLine,
    User,
    next_number,
)
from services.catalog import tax_for_product, unit_price_for
from services.dates import DEFAULT_TERM, add_periods

DEMO_PASSWORDS = {"admin": "Admin@12345", "staff": "Staff@12345", "customer": "Customer@123"}

CUSTOMERS = [
    ("Arjun Das", "customer@demo.com", "Malabar Spice Traders", "Fort Road, Kannur, Kerala 670001"),
    ("Meera Pillai", "meera@coastlineclinics.in", "Coastline Clinics", "MG Road, Kochi, Kerala 682016"),
    ("Vikram Shetty", "vikram@northwindlogistics.in", "Northwind Logistics", "Bunder, Mangaluru, Karnataka 575001"),
    ("Neha Joseph", "neha@kochicraft.in", "Kochi Craft Studio", "Fort Kochi, Kerala 682001"),
    ("Sameer Ali", "sameer@greenleaforganics.in", "Greenleaf Organics", "Thalassery, Kerala 670101"),
    ("Priya Varma", "priya@bluewaveretail.in", "Bluewave Retail", "Indiranagar, Bengaluru 560038"),
    ("Kiran Rao", "kiran@summitfitness.in", "Summit Fitness", "Koramangala, Bengaluru 560034"),
    ("Thomas Mathew", "thomas@harborhotels.in", "Harbor Hotels", "Marine Drive, Kochi 682031"),
    ("Ananya Iyer", "ananya@pixelforge.in", "Pixel Forge Media", "Anna Nagar, Chennai 600040"),
    ("Joseph Kurian", "joseph@teavalley.in", "Tea Valley Exports", "Munnar, Kerala 685612"),
    ("Lakshmi Prasad", "lakshmi@urbanbake.in", "Urban Bake House", "Kozhikode, Kerala 673001"),
    ("Faisal Rahman", "faisal@metromotors.in", "Metro Motors", "Thrissur, Kerala 680001"),
]


def _user(name, email, role, company=None, address=None, phone=None):
    user = User(name=name, email=email, role=role, company=company, address=address, phone=phone)
    user.set_password(DEMO_PASSWORDS[role])
    db.session.add(user)
    return user


def seed_demo_data(bulk: int = 0) -> None:
    today = date.today()
    year_start = date(today.year, 1, 1)

    # --- People -------------------------------------------------------------
    admin = _user("Anita Menon", "admin@brightloop.com", "admin", phone="+91 98470 11111")
    _user("Rahul Nair", "staff@brightloop.com", "staff", phone="+91 98470 22222")
    _user("Fathima Kareem", "fathima@brightloop.com", "staff", phone="+91 98470 33333")
    customers = [
        _user(name, email, "customer", company=company, address=address, phone=f"+91 94470 {10000 + i * 731:05d}")
        for i, (name, email, company, address) in enumerate(CUSTOMERS)
    ]
    customers[0].gstin = "32AAACM1234K1Z2"

    # --- Taxes --------------------------------------------------------------
    gst18 = Tax(name="GST 18%", rate=Decimal("18"), is_default=True, description="Standard rate for software services")
    gst12 = Tax(name="GST 12%", rate=Decimal("12"), description="Hardware rentals")
    gst5 = Tax(name="GST 5%", rate=Decimal("5"), description="Reduced rate")
    exempt = Tax(name="Exempt", rate=Decimal("0"), description="Zero-rated items")
    db.session.add_all([gst18, gst12, gst5, exempt])

    # --- Products and variants ---------------------------------------------
    crm = Product(name="Brightloop CRM", product_type="service", sales_price=Decimal("1200"), cost_price=Decimal("300"),
                  tax=gst18, notes="Per user, per billing period. Includes mobile app.")
    crm.variants = [
        ProductVariant(attribute="Brand", value="Brightloop", extra_price=Decimal("0")),
        ProductVariant(attribute="Brand", value="Odoo", extra_price=Decimal("560")),
    ]
    accounting = Product(name="Brightloop Accounting", product_type="service", sales_price=Decimal("1500"),
                         cost_price=Decimal("450"), tax=gst18, notes="GST-ready books, invoicing and bank sync.")
    accounting.variants = [
        ProductVariant(attribute="Edition", value="Standard", extra_price=Decimal("0")),
        ProductVariant(attribute="Edition", value="Professional", extra_price=Decimal("700")),
    ]
    inventory = Product(name="Inventory add-on", product_type="addon", sales_price=Decimal("499"),
                        cost_price=Decimal("120"), tax=gst18, notes="Stock levels, transfers and reorder alerts.")
    storage = Product(name="Extra storage (50 GB)", product_type="addon", sales_price=Decimal("199"),
                      cost_price=Decimal("40"), tax=gst18)
    support = Product(name="Priority support", product_type="service", sales_price=Decimal("999"),
                      cost_price=Decimal("300"), tax=gst18, notes="4-hour response, named support engineer.")
    scanner = Product(name="Barcode scanner (rental)", product_type="goods", sales_price=Decimal("350"),
                      cost_price=Decimal("150"), tax=gst12, notes="Wireless scanner, replaced free if faulty.")
    db.session.add_all([crm, accounting, inventory, storage, support, scanner])

    # --- Plans --------------------------------------------------------------
    monthly = Plan(name="Monthly Standard", price=Decimal("299"), billing_period="monthly", min_quantity=1,
                   start_date=year_start, closable=True, pausable=True, renewable=True, auto_close=False,
                   description="Flexible monthly billing. Pause or close any time.")
    yearly = Plan(name="Yearly Business", price=Decimal("2999"), billing_period="yearly", min_quantity=5,
                  start_date=year_start, closable=False, pausable=False, renewable=True, auto_close=True,
                  description="Annual contract for 5 or more users. Closes automatically at the end of the term.")
    weekly = Plan(name="Weekly Trial", price=Decimal("0"), billing_period="weekly", min_quantity=1,
                  start_date=year_start, end_date=date(today.year, 12, 31), closable=True, pausable=False,
                  renewable=False, auto_close=True, description="Short trial billed weekly.")
    daily = Plan(name="Daily Pay-as-you-go", price=Decimal("49"), billing_period="daily", min_quantity=1,
                 start_date=year_start, closable=True, pausable=True, renewable=True,
                 description="For seasonal businesses. Billed every day it is active.")
    legacy = Plan(name="Legacy Monthly 2025", price=Decimal("199"), billing_period="monthly", min_quantity=1,
                  start_date=date(today.year - 1, 1, 1), end_date=date(today.year - 1, 12, 31), active=True,
                  description="Old price list. No longer available for new subscriptions.")
    db.session.add_all([monthly, yearly, weekly, daily, legacy])

    # --- Discounts (admin only) ----------------------------------------------
    db.session.add_all([
        Discount(name="Bulk seats 10%", discount_type="percent", value=Decimal("10"), min_quantity=10,
                 applies_to="subscription", start_date=today - timedelta(days=60), end_date=today + timedelta(days=90),
                 usage_limit=100, created_by=admin),
        Discount(name="Festive ₹500 off CRM", discount_type="fixed", value=Decimal("500"), min_purchase=Decimal("2000"),
                 applies_to="products", products=[crm], start_date=today - timedelta(days=15),
                 end_date=today + timedelta(days=20), usage_limit=25, created_by=admin),
        Discount(name="Monsoon 15%", discount_type="percent", value=Decimal("15"), applies_to="subscription",
                 start_date=date(today.year, 6, 1), end_date=date(today.year, 8, 31), created_by=admin),
        Discount(name="Launch week 20%", discount_type="percent", value=Decimal("20"), applies_to="subscription",
                 start_date=year_start, end_date=date(today.year, 12, 31), usage_limit=3, times_used=3,
                 created_by=admin),
    ])

    # --- Quotation templates ---------------------------------------------------
    starter = QuotationTemplate(name="CRM Starter", validity_days=15, plan=monthly,
                                notes="For small teams moving off spreadsheets.")
    starter.lines = [
        TemplateLine(product=crm, variant=crm.variants[0], quantity=3),
        TemplateLine(product=storage, quantity=1),
    ]
    enterprise = QuotationTemplate(name="Enterprise Suite", validity_days=30, plan=yearly,
                                   notes="CRM on the Odoo brand plus accounting, for larger companies.")
    enterprise.lines = [
        TemplateLine(product=crm, variant=crm.variants[1], quantity=20),
        TemplateLine(product=accounting, variant=accounting.variants[1], quantity=10),
        TemplateLine(product=support, quantity=1),
    ]
    retail = QuotationTemplate(name="Retail Store Kit", validity_days=10, plan=monthly,
                               notes="Accounting with inventory and a scanner for shops.")
    retail.lines = [
        TemplateLine(product=accounting, variant=accounting.variants[0], quantity=2),
        TemplateLine(product=inventory, quantity=1),
        TemplateLine(product=scanner, quantity=2),
    ]
    db.session.add_all([starter, enterprise, retail])
    db.session.commit()

    _seed_activity(customers, admin, bulk)


def _seed_activity(customers, admin, bulk):
    """Subscriptions in every stage, so each screen has something to show."""
    from services.subscriptions import log, recompute_totals

    today = date.today()
    staff = User.query.filter_by(email="staff@brightloop.com").one()
    plans = {p.name: p for p in Plan.query.all()}
    products = {p.name: p for p in Product.query.all()}
    templates = {t.name: t for t in QuotationTemplate.query.all()}
    crm, accounting = products["Brightloop CRM"], products["Brightloop Accounting"]
    crm_plain, crm_odoo = crm.variants
    acc_std, acc_pro = accounting.variants

    def line(product, qty, variant=None):
        return SubscriptionLine(product=product, variant=variant, quantity=qty, enabled=True,
                                unit_price=unit_price_for(product, variant), tax=tax_for_product(product))

    def make(customer, plan_name, start, lines, status, expiry=None, terms=15, template=None, note=None,
             valid_until=None, reason=None):
        plan = plans[plan_name]
        if expiry is None and status != "draft":
            expiry = add_periods(start, plan.billing_period, DEFAULT_TERM[plan.billing_period]) - timedelta(days=1)
        sub = Subscription(number=next_number("SUB"), customer=customer, plan=plan, start_date=start,
                           expiry_date=expiry, payment_term_days=terms, status="draft", created_by=staff,
                           template=template, notes=note, quotation_valid_until=valid_until, lines=lines)
        sub.created_at = datetime.combine(start - timedelta(days=7), datetime.min.time()).replace(hour=10)
        log(sub, "Created" + (f" from template {template.name}" if template else ""), staff, to_status="draft")
        path = {"draft": [], "quotation": ["quotation"], "confirmed": ["quotation", "confirmed"],
                "active": ["quotation", "confirmed", "active"], "paused": ["quotation", "confirmed", "active", "paused"],
                "closed": ["quotation", "confirmed", "active", "closed"]}[status]
        messages = {"quotation": f"Quotation sent to {customer.email}", "confirmed": "Confirmed by customer",
                    "active": "Started", "paused": "Paused: billing stops until resumed",
                    "closed": "Closed" + (f": {reason}" if reason else "")}
        previous = "draft"
        for step in path:
            log(sub, messages[step], staff, from_status=previous, to_status=step)
            previous = step
        sub.status = status
        if status in ("confirmed", "active", "paused", "closed"):
            sub.confirmed_at = sub.created_at + timedelta(days=2)
            sub.next_invoice_date = start
        if status in ("active", "paused", "closed"):
            sub.activated_at = datetime.combine(start, datetime.min.time())
        if status == "closed":
            sub.closed_at, sub.close_reason = datetime.now() - timedelta(days=12), reason
        for i, entry in enumerate(sorted(sub.logs, key=lambda e: e.id or 0)):
            entry.created_at = sub.created_at + timedelta(days=i)
        recompute_totals(sub)
        db.session.add(sub)
        return sub

    c = customers
    month_start = today.replace(day=1)
    four_months_ago = add_periods(month_start, "monthly", -4)
    make(c[0], "Monthly Standard", four_months_ago,
         [line(crm, 3, crm_plain), line(products["Extra storage (50 GB)"], 1)], "active", terms=15)
    make(c[0], "Monthly Standard", today + timedelta(days=3),
         [line(accounting, 2, acc_std), line(products["Inventory add-on"], 1),
          line(products["Barcode scanner (rental)"], 2)], "quotation", template=templates["Retail Store Kit"],
         valid_until=today + timedelta(days=8), note="For the new Thalassery branch.")
    make(c[1], "Yearly Business", today - timedelta(days=200),
         [line(crm, 12, crm_odoo), line(accounting, 5, acc_pro)], "active", terms=30)
    make(c[2], "Monthly Standard", add_periods(month_start, "monthly", -3),
         [line(accounting, 4, acc_std), line(products["Inventory add-on"], 1),
          line(products["Barcode scanner (rental)"], 3)], "active", terms=7)
    make(c[3], "Weekly Trial", today - timedelta(days=10), [line(crm, 1, crm_plain)], "active", terms=0)
    make(c[4], "Monthly Standard", add_periods(month_start, "monthly", -2),
         [line(crm, 2, crm_plain)], "paused", terms=15)
    make(c[5], "Monthly Standard", add_periods(today, "monthly", -2),
         [line(crm, 12, crm_odoo), line(products["Priority support"], 1)], "active", terms=15)
    make(c[6], "Daily Pay-as-you-go", today - timedelta(days=4), [line(crm, 2, crm_plain)], "active", terms=0)
    make(c[7], "Yearly Business", today + timedelta(days=14),
         [line(crm, 20, crm_odoo), line(accounting, 10, acc_pro), line(products["Priority support"], 1)],
         "draft", template=templates["Enterprise Suite"], valid_until=today + timedelta(days=30))
    make(c[8], "Monthly Standard", today + timedelta(days=5),
         [line(crm, 4, crm_plain), line(products["Priority support"], 1)], "confirmed", terms=15)
    make(c[9], "Monthly Standard", add_periods(month_start, "monthly", -5),
         [line(accounting, 1, acc_std)], "closed", reason="Moved to an annual contract")
    make(c[10], "Yearly Business", add_periods(today, "yearly", -1),
         [line(crm, 6, crm_plain)], "active", expiry=today - timedelta(days=1), terms=30)
    make(c[11], "Monthly Standard", add_periods(today, "monthly", -1) + timedelta(days=2),
         [line(crm, 2, crm_plain)], "active", terms=15)
    db.session.commit()


def reset_database(bulk: int = 0) -> None:
    db.drop_all()
    db.create_all()
    seed_demo_data(bulk=bulk)


if __name__ == "__main__":
    os.environ["AUTO_SEED"] = "0"  # we seed explicitly below
    from app import create_app

    bulk_count = 0
    if "--bulk" in sys.argv:
        try:
            bulk_count = int(sys.argv[sys.argv.index("--bulk") + 1])
        except (IndexError, ValueError):
            bulk_count = 3000
    flask_app = create_app()
    with flask_app.app_context():
        reset_database(bulk=bulk_count)
    print("Demo data loaded.")
    print("  Admin:    admin@brightloop.com / Admin@12345")
    print("  Staff:    staff@brightloop.com / Staff@12345")
    print("  Customer: customer@demo.com / Customer@123")
