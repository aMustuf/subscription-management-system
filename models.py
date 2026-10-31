"""Database models for the whole system (SQLite through SQLAlchemy).

Tables
  users                 admin, staff and customers (role column)
  taxes                 tax rules set by the admin
  products              things Brightloop sells
  product_variants      e.g. Brand: Odoo (+560)
  plans                 recurring plans: price, billing period, rules
  quotation_templates   ready-made offers (plan + product lines)
  subscriptions         one per customer contract, numbered SUB/2026/00001
  subscription_lines    product, quantity, unit price, tax
  subscription_logs     status history ("where it stands")
  discounts             admin-only discount rules
  invoices              created from subscriptions, numbered INV/2026/00001
  invoice_lines         copied from the subscription when billing
  invoice_discounts     which discount rules were applied to an invoice
  payments              money received against an invoice
  email_logs            every email the system sends (the Email outbox)
  sequences             counters for document numbers
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Round any number to 2 decimal places as a Decimal (safe for money)."""
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Choice lists (value -> label shown in the UI)
# ---------------------------------------------------------------------------
ROLES = {"admin": "Admin", "staff": "Staff", "customer": "Customer"}
PRODUCT_TYPES = {"service": "Service", "goods": "Goods", "addon": "Add-on"}
BILLING_PERIODS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "yearly": "Yearly"}
PERIOD_UNITS = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}
SUBSCRIPTION_STATUSES = {
    "draft": "Draft",
    "quotation": "Quotation",
    "confirmed": "Confirmed",
    "active": "Active",
    "paused": "Paused",
    "closed": "Closed",
}
LIFECYCLE = ["draft", "quotation", "confirmed", "active", "closed"]
INVOICE_STATUSES = {"draft": "Draft", "confirmed": "Confirmed", "paid": "Paid", "cancelled": "Cancelled"}
PAYMENT_METHODS = {
    "bank_transfer": "Bank transfer",
    "upi": "UPI",
    "card": "Card",
    "cash": "Cash",
    "cheque": "Cheque",
    "online": "Online (portal)",
}
PAYMENT_TERMS = {0: "Due on receipt", 7: "7 days", 15: "15 days", 30: "30 days", 45: "45 days", 60: "60 days"}
DISCOUNT_TYPES = {"percent": "Percentage", "fixed": "Fixed amount"}
DISCOUNT_SCOPES = {"subscription": "Whole subscription", "products": "Specific products"}


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'staff', 'customer')", name="ck_users_role"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)  # one account per email
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="customer", index=True)
    phone = db.Column(db.String(30))
    company = db.Column(db.String(150))
    address = db.Column(db.Text)
    gstin = db.Column(db.String(20))
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(db.DateTime)

    # Read-only collections (records are linked by setting subscription.customer / invoice.customer)
    subscriptions = db.relationship(
        "Subscription", foreign_keys="Subscription.customer_id", lazy="dynamic", viewonly=True
    )
    invoices = db.relationship("Invoice", foreign_keys="Invoice.customer_id", lazy="dynamic", viewonly=True)

    # Flask-Login checks this to block deactivated accounts
    @property
    def is_active(self):
        return bool(self.is_active_account)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_staff(self):
        """Admin and staff both count as the internal team."""
        return self.role in ("admin", "staff")

    @property
    def is_customer(self):
        return self.role == "customer"

    @property
    def display_name(self):
        return self.company or self.name

    @property
    def role_label(self):
        return ROLES.get(self.role, self.role)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


# ---------------------------------------------------------------------------
# Catalog: taxes, products, variants, plans
# ---------------------------------------------------------------------------
class Tax(TimestampMixin, db.Model):
    __tablename__ = "taxes"
    __table_args__ = (CheckConstraint("rate >= 0 AND rate <= 100", name="ck_taxes_rate"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False, unique=True)
    rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)  # percent, e.g. 18.00
    description = db.Column(db.String(255))
    is_default = db.Column(db.Boolean, nullable=False, default=False)  # used when a product has no tax
    active = db.Column(db.Boolean, nullable=False, default=True)

    def compute(self, amount) -> Decimal:
        return money(money(amount) * money(self.rate) / Decimal(100))

    @property
    def label(self):
        return f"{self.name}"


class Product(TimestampMixin, db.Model):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("sales_price >= 0", name="ck_products_sales_price"),
        CheckConstraint("cost_price >= 0", name="ck_products_cost_price"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    product_type = db.Column(db.String(20), nullable=False, default="service")
    sales_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    cost_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax_id = db.Column(db.Integer, db.ForeignKey("taxes.id"))
    notes = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)

    tax = db.relationship("Tax")
    variants = db.relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan", order_by="ProductVariant.id"
    )

    @property
    def type_label(self):
        return PRODUCT_TYPES.get(self.product_type, self.product_type)

    @property
    def margin(self):
        return money(self.sales_price) - money(self.cost_price)

    @property
    def active_variants(self):
        return [v for v in self.variants if v.active]


class ProductVariant(TimestampMixin, db.Model):
    """One option of a product, e.g. attribute 'Brand' value 'Odoo' adds 560 to the price."""

    __tablename__ = "product_variants"
    __table_args__ = (UniqueConstraint("product_id", "attribute", "value", name="uq_variant_value"),)

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    attribute = db.Column(db.String(60), nullable=False)
    value = db.Column(db.String(80), nullable=False)
    extra_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True)

    product = db.relationship("Product", back_populates="variants")

    @property
    def label(self):
        return f"{self.attribute}: {self.value}"

    @property
    def price(self):
        return money(self.product.sales_price) + money(self.extra_price)


class Plan(TimestampMixin, db.Model):
    """A recurring plan: what it costs, how often it bills and what customers may do with it."""

    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_plans_price"),
        CheckConstraint("min_quantity >= 1", name="ck_plans_min_quantity"),
        CheckConstraint("billing_period IN ('daily', 'weekly', 'monthly', 'yearly')", name="ck_plans_period"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # plan fee charged every period
    billing_period = db.Column(db.String(10), nullable=False, default="monthly")
    min_quantity = db.Column(db.Integer, nullable=False, default=1)
    start_date = db.Column(db.Date)  # available from
    end_date = db.Column(db.Date)  # available until
    auto_close = db.Column(db.Boolean, nullable=False, default=False)  # close subscriptions at expiry
    closable = db.Column(db.Boolean, nullable=False, default=True)  # can be closed early
    pausable = db.Column(db.Boolean, nullable=False, default=False)
    renewable = db.Column(db.Boolean, nullable=False, default=True)
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)

    subscriptions = db.relationship("Subscription", lazy="dynamic", viewonly=True)

    @property
    def period_label(self):
        return BILLING_PERIODS.get(self.billing_period, self.billing_period)

    @property
    def period_unit(self):
        return PERIOD_UNITS.get(self.billing_period, self.billing_period)

    def is_available_on(self, day: date) -> bool:
        if self.start_date and day < self.start_date:
            return False
        if self.end_date and day > self.end_date:
            return False
        return True

    @property
    def availability_label(self):
        if self.start_date and self.end_date:
            return f"{self.start_date:%d %b %Y} – {self.end_date:%d %b %Y}"
        if self.start_date:
            return f"From {self.start_date:%d %b %Y}"
        if self.end_date:
            return f"Until {self.end_date:%d %b %Y}"
        return "Always"


# ---------------------------------------------------------------------------
# Quotation templates
# ---------------------------------------------------------------------------
class QuotationTemplate(TimestampMixin, db.Model):
    __tablename__ = "quotation_templates"
    __table_args__ = (CheckConstraint("validity_days >= 1", name="ck_templates_validity"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    validity_days = db.Column(db.Integer, nullable=False, default=30)
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id"), nullable=False)
    notes = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)

    plan = db.relationship("Plan")
    lines = db.relationship(
        "TemplateLine", back_populates="template", cascade="all, delete-orphan", order_by="TemplateLine.id"
    )


class TemplateLine(db.Model):
    __tablename__ = "template_lines"
    __table_args__ = (CheckConstraint("quantity >= 1", name="ck_template_lines_qty"),)

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("quotation_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"))
    quantity = db.Column(db.Integer, nullable=False, default=1)

    template = db.relationship("QuotationTemplate", back_populates="lines")
    product = db.relationship("Product")
    variant = db.relationship("ProductVariant")


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
class Subscription(TimestampMixin, db.Model):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'quotation', 'confirmed', 'active', 'paused', 'closed')", name="ck_subs_status"
        ),
        CheckConstraint("expiry_date IS NULL OR expiry_date >= start_date", name="ck_subs_dates"),
        Index("ix_subs_status_next_invoice", "status", "next_invoice_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), nullable=False, unique=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id"), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("quotation_templates.id"))
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    start_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, index=True)
    payment_term_days = db.Column(db.Integer, nullable=False, default=0)
    next_invoice_date = db.Column(db.Date)
    quotation_valid_until = db.Column(db.Date)
    notes = db.Column(db.Text)

    # Per-period amounts, recalculated whenever lines change (fast lists and reports)
    amount_untaxed = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount_tax = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    confirmed_at = db.Column(db.DateTime)
    activated_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    close_reason = db.Column(db.String(255))

    customer = db.relationship("User", foreign_keys=[customer_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    plan = db.relationship("Plan")
    template = db.relationship("QuotationTemplate")
    lines = db.relationship(
        "SubscriptionLine", back_populates="subscription", cascade="all, delete-orphan", order_by="SubscriptionLine.id"
    )
    invoices = db.relationship("Invoice", lazy="dynamic", viewonly=True)
    logs = db.relationship(
        "SubscriptionLog",
        back_populates="subscription",
        cascade="all, delete-orphan",
        order_by="SubscriptionLog.created_at.desc()",
    )

    @property
    def status_label(self):
        return SUBSCRIPTION_STATUSES.get(self.status, self.status)

    @property
    def payment_terms_label(self):
        return PAYMENT_TERMS.get(self.payment_term_days, f"{self.payment_term_days} days")

    @property
    def is_quotation_expired(self):
        return (
            self.status in ("draft", "quotation")
            and self.quotation_valid_until is not None
            and self.quotation_valid_until < date.today()
        )

    @property
    def is_expired(self):
        return self.expiry_date is not None and self.expiry_date < date.today()

    @property
    def billable_lines(self):
        return [line for line in self.lines if line.enabled is not False]


class SubscriptionLine(db.Model):
    __tablename__ = "subscription_lines"
    __table_args__ = (
        CheckConstraint("quantity >= 1", name="ck_sub_lines_qty"),
        CheckConstraint("unit_price >= 0", name="ck_sub_lines_price"),
    )

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"))
    description = db.Column(db.String(255))
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax_id = db.Column(db.Integer, db.ForeignKey("taxes.id"))
    enabled = db.Column(db.Boolean, nullable=False, default=True)  # add-ons can be switched off

    subscription = db.relationship("Subscription", back_populates="lines")
    product = db.relationship("Product")
    variant = db.relationship("ProductVariant")
    tax = db.relationship("Tax")

    @property
    def label(self):
        if self.description:
            return self.description
        name = self.product.name if self.product else "Item"
        return f"{name} ({self.variant.label})" if self.variant else name

    @property
    def amount(self):
        return money(money(self.unit_price) * self.quantity)

    @property
    def tax_amount(self):
        return self.tax.compute(self.amount) if self.tax else ZERO

    @property
    def total(self):
        return self.amount + self.tax_amount


class SubscriptionLog(db.Model):
    """Every status change, so anyone on the team can see where a subscription stands."""

    __tablename__ = "subscription_logs"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status = db.Column(db.String(20))
    to_status = db.Column(db.String(20))
    message = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    subscription = db.relationship("Subscription", back_populates="logs")
    user = db.relationship("User")


# ---------------------------------------------------------------------------
# Discounts
# ---------------------------------------------------------------------------
discount_products = db.Table(
    "discount_products",
    db.Column("discount_id", db.Integer, db.ForeignKey("discounts.id", ondelete="CASCADE"), primary_key=True),
    db.Column("product_id", db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
)


class Discount(TimestampMixin, db.Model):
    __tablename__ = "discounts"
    __table_args__ = (
        CheckConstraint("value > 0", name="ck_discounts_value"),
        CheckConstraint("discount_type IN ('percent', 'fixed')", name="ck_discounts_type"),
        CheckConstraint("applies_to IN ('subscription', 'products')", name="ck_discounts_scope"),
        CheckConstraint("end_date >= start_date", name="ck_discounts_dates"),
        CheckConstraint("usage_limit IS NULL OR usage_limit >= 1", name="ck_discounts_limit"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    discount_type = db.Column(db.String(10), nullable=False, default="percent")
    value = db.Column(db.Numeric(12, 2), nullable=False)
    min_purchase = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    min_quantity = db.Column(db.Integer, nullable=False, default=0)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    usage_limit = db.Column(db.Integer)  # empty = unlimited
    times_used = db.Column(db.Integer, nullable=False, default=0)
    applies_to = db.Column(db.String(15), nullable=False, default="subscription")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    products = db.relationship("Product", secondary=discount_products, order_by="Product.name")
    created_by = db.relationship("User")

    @property
    def value_label(self):
        if self.discount_type == "percent":
            return f"{money(self.value).normalize():f}%"
        return None  # formatted as money in templates

    @property
    def scope_label(self):
        return DISCOUNT_SCOPES.get(self.applies_to, self.applies_to)

    @property
    def uses_left(self):
        if self.usage_limit is None:
            return None
        return max(self.usage_limit - self.times_used, 0)

    def state(self, today: date | None = None) -> str:
        today = today or date.today()
        if not self.active:
            return "disabled"
        if self.usage_limit is not None and self.times_used >= self.usage_limit:
            return "used_up"
        if today < self.start_date:
            return "scheduled"
        if today > self.end_date:
            return "expired"
        return "running"

    def is_running(self, today: date | None = None) -> bool:
        return self.state(today) == "running"


# ---------------------------------------------------------------------------
# Invoices and payments
# ---------------------------------------------------------------------------
class Invoice(TimestampMixin, db.Model):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'confirmed', 'paid', 'cancelled')", name="ck_invoices_status"),
        CheckConstraint("amount_paid >= 0", name="ck_invoices_paid"),
        # Never bill the same subscription period twice (cancelled invoices don't count)
        Index(
            "uq_invoices_subscription_period",
            "subscription_id",
            "period_start",
            unique=True,
            sqlite_where=text("status != 'cancelled'"),
        ),
        Index("ix_invoices_status_due", "status", "due_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), nullable=False, unique=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscriptions.id"), index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    invoice_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    due_date = db.Column(db.Date, nullable=False)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)

    # Customer details copied at billing time, so old invoices never change
    bill_to_name = db.Column(db.String(150))
    bill_to_company = db.Column(db.String(150))
    bill_to_email = db.Column(db.String(255))
    bill_to_address = db.Column(db.Text)
    bill_to_gstin = db.Column(db.String(20))

    amount_untaxed = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # before discounts
    amount_discount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount_tax = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount_paid = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    notes = db.Column(db.Text)
    sent_at = db.Column(db.DateTime)
    confirmed_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)

    subscription = db.relationship("Subscription")
    customer = db.relationship("User", foreign_keys=[customer_id])
    lines = db.relationship(
        "InvoiceLine", back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.id"
    )
    applied_discounts = db.relationship(
        "InvoiceDiscount", back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceDiscount.id"
    )
    payments = db.relationship(
        "Payment", back_populates="invoice", cascade="all, delete-orphan", order_by="Payment.payment_date.desc()"
    )

    @property
    def status_label(self):
        return INVOICE_STATUSES.get(self.status, self.status)

    @property
    def amount_due(self):
        if self.status == "cancelled":
            return ZERO
        return max(money(self.amount_total) - money(self.amount_paid), ZERO)

    @property
    def is_overdue(self):
        return self.status == "confirmed" and self.due_date < date.today() and self.amount_due > 0

    @property
    def days_overdue(self):
        return (date.today() - self.due_date).days if self.is_overdue else 0

    @property
    def amount_taxable(self):
        return money(self.amount_untaxed) - money(self.amount_discount)


class InvoiceLine(db.Model):
    __tablename__ = "invoice_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax_name = db.Column(db.String(60))
    tax_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # quantity x unit price
    tax_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # after discount, with tax

    invoice = db.relationship("Invoice", back_populates="lines")
    product = db.relationship("Product")


class InvoiceDiscount(db.Model):
    __tablename__ = "invoice_discounts"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    discount_id = db.Column(db.Integer, db.ForeignKey("discounts.id"), index=True)
    name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    invoice = db.relationship("Invoice", back_populates="applied_discounts")
    discount = db.relationship("Discount")


class Payment(TimestampMixin, db.Model):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_payments_amount"),)

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), nullable=False, unique=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, index=True)
    reference = db.Column(db.String(80))
    note = db.Column(db.Text)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    invoice = db.relationship("Invoice", back_populates="payments")
    recorded_by = db.relationship("User")

    @property
    def method_label(self):
        return PAYMENT_METHODS.get(self.method, self.method)


# ---------------------------------------------------------------------------
# Email outbox and document numbering
# ---------------------------------------------------------------------------
class EmailLog(db.Model):
    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)
    to_email = db.Column(db.String(255), nullable=False, index=True)
    subject = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(30), nullable=False, default="general")
    status = db.Column(db.String(20), nullable=False, default="logged")  # sent / logged / failed
    error = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)


class Sequence(db.Model):
    __tablename__ = "sequences"
    __table_args__ = (UniqueConstraint("name", "year", name="uq_sequences_name_year"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    value = db.Column(db.Integer, nullable=False, default=0)


def next_number(prefix: str, on_date: date | None = None) -> str:
    """Return the next document number, e.g. SUB/2026/00042. Never reused."""
    year = (on_date or date.today()).year
    seq = Sequence.query.filter_by(name=prefix, year=year).first()
    if seq is None:
        seq = Sequence(name=prefix, year=year, value=0)
        db.session.add(seq)
    seq.value += 1
    db.session.flush()
    return f"{prefix}/{year}/{seq.value:05d}"
