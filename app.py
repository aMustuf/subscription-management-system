"""Brightloop Subscriptions: Flask entry point.

Run it with:   python app.py      (then open http://127.0.0.1:5000)
"""
from __future__ import annotations

import os
import time
from datetime import date

from flask import Flask, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.engine import Engine

from config import Config
from extensions import csrf, db, login_manager
from models import (
    BILLING_PERIODS,
    INVOICE_STATUSES,
    PAYMENT_METHODS,
    PAYMENT_TERMS,
    PRODUCT_TYPES,
    SUBSCRIPTION_STATUSES,
    User,
)
from utils import format_date, format_datetime, format_money, home_endpoint, page_args

# Sidebar navigation: (section, [(label, endpoint, icon, roles)])
STAFF = ("admin", "staff")
ADMIN = ("admin",)
NAVIGATION = [
    ("Overview", [("Dashboard", "main.dashboard", "speedometer2", STAFF)]),
    (
        "Sales",
        [
            ("Subscriptions", "subscriptions.index", "arrow-repeat", STAFF),
            ("Quotation templates", "quotes.index", "file-earmark-text", STAFF),
            ("Customers", "customers.index", "people", STAFF),
        ],
    ),
    (
        "Billing",
        [
            ("Invoices", "invoices.index", "receipt", STAFF),
            ("Payments", "payments.index", "cash-coin", STAFF),
            ("Billing run", "billing.run", "lightning-charge", STAFF),
            ("Reports", "reports.index", "bar-chart-line", STAFF),
        ],
    ),
    (
        "Catalog",
        [
            ("Products", "products.index", "box-seam", STAFF),
            ("Plans", "plans.index", "calendar3", STAFF),
            ("Discounts", "discounts.index", "percent", STAFF),
            ("Taxes", "taxes.index", "bank", ADMIN),
        ],
    ),
    (
        "Admin",
        [
            ("Team", "team.index", "person-badge", ADMIN),
            ("Email outbox", "outbox.index", "envelope", STAFF),
        ],
    ),
]
PORTAL_NAVIGATION = [
    (
        "My account",
        [
            ("Overview", "portal.home", "house", ("customer",)),
            ("Subscriptions", "portal.subscriptions", "arrow-repeat", ("customer",)),
            ("Invoices", "portal.invoices", "receipt", ("customer",)),
        ],
    )
]


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """Turn on foreign keys (real referential integrity) and WAL for speed."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Log in to continue."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    register_blueprints(app)
    register_template_helpers(app)
    register_error_pages(app)
    register_request_hooks(app)

    with app.app_context():
        db.create_all()
        if app.config.get("AUTO_SEED", True) and User.query.count() == 0:
            from seed import seed_demo_data

            seed_demo_data()
            print(" * Demo data created. Log in as admin@brightloop.com / Admin@12345")

    return app


def register_blueprints(app: Flask) -> None:
    import routes

    for blueprint in routes.all_blueprints():
        app.register_blueprint(blueprint)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for(home_endpoint(current_user)))
        return redirect(url_for("auth.login"))


def register_template_helpers(app: Flask) -> None:
    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["date"] = format_date
    app.jinja_env.filters["datetime"] = format_datetime

    @app.context_processor
    def inject_globals():
        def endpoint_exists(endpoint):
            return endpoint in app.view_functions

        nav = []
        if current_user.is_authenticated:
            source = PORTAL_NAVIGATION if current_user.is_customer else NAVIGATION
            for section, items in source:
                visible = [
                    (label, endpoint, icon)
                    for label, endpoint, icon, roles in items
                    if current_user.role in roles and endpoint_exists(endpoint)
                ]
                if visible:
                    nav.append((section, visible))
        current_bp = request.blueprint or ""
        return {
            "navigation": nav,
            "current_blueprint": current_bp,
            "endpoint_exists": endpoint_exists,
            "page_args": page_args,
            "today": date.today(),
            "company": {
                "name": app.config["COMPANY_NAME"],
                "address": app.config["COMPANY_ADDRESS"],
                "email": app.config["COMPANY_EMAIL"],
                "gstin": app.config["COMPANY_GSTIN"],
            },
            "SUBSCRIPTION_STATUSES": SUBSCRIPTION_STATUSES,
            "INVOICE_STATUSES": INVOICE_STATUSES,
            "BILLING_PERIODS": BILLING_PERIODS,
            "PAYMENT_METHODS": PAYMENT_METHODS,
            "PAYMENT_TERMS": PAYMENT_TERMS,
            "PRODUCT_TYPES": PRODUCT_TYPES,
        }


def register_error_pages(app: Flask) -> None:
    messages = {
        403: ("You don't have access to this page", "Your role doesn't include this page. Ask an admin if you need it."),
        404: ("Page not found", "The link may be old, or the record was removed."),
        500: ("Something went wrong", "The error was logged. Go back and try again."),
    }

    def make_handler(code):
        def handler(_error):
            if code == 500:
                db.session.rollback()
            title, detail = messages[code]
            return render_template("errors/error.html", code=code, title=title, detail=detail), code

        return handler

    for code in messages:
        app.register_error_handler(code, make_handler(code))


def register_request_hooks(app: Flask) -> None:
    @app.before_request
    def start_timer():
        g.request_started = time.perf_counter()
        # Daily housekeeping (auto-activate / auto-close subscriptions), at most once a minute
        if current_user.is_authenticated and "housekeeping" in app.extensions:
            app.extensions["housekeeping"]()

    @app.after_request
    def add_timing_header(response):
        started = g.get("request_started")
        if started is not None:
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        return response


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
