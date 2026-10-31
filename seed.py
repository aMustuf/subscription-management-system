"""Demo data for Brightloop Software.

    python seed.py            wipe the database and load fresh demo data
    python seed.py --bulk 3000   also add 3,000 extra subscriptions (speed test)

All dates are relative to today, so the demo always looks current.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

from extensions import db
from models import (
    Discount,
    Plan,
    Product,
    ProductVariant,
    QuotationTemplate,
    Tax,
    TemplateLine,
    User,
)

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
    """Subscriptions, invoices and payments (added in later milestones)."""
    return None


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
