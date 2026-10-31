"""Catalog helpers: product pickers, default prices and taxes, and reading line editors."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import joinedload, selectinload

from extensions import db
from models import Product, ProductVariant, Tax, money

MAX_QTY = 100_000
MAX_PRICE = Decimal("10000000")


def default_tax() -> Tax | None:
    return Tax.query.filter_by(is_default=True, active=True).first()


def tax_for_product(product: Product) -> Tax | None:
    """The admin's tax rule: the product's own tax, otherwise the default tax."""
    if product.tax is not None and product.tax.active:
        return product.tax
    return default_tax()


def unit_price_for(product: Product, variant: ProductVariant | None) -> Decimal:
    return variant.price if variant is not None else money(product.sales_price)


def item_key(product_id, variant_id) -> str:
    return f"{product_id}:{variant_id or 0}"


def catalog_items(include_keys=()) -> list[dict]:
    """Options for the line pickers: one per product, or one per variant if the product has variants."""
    fallback = default_tax()
    include_keys = set(include_keys)
    products = (
        Product.query.options(selectinload(Product.variants), joinedload(Product.tax)).order_by(Product.name).all()
    )
    items = []
    for product in products:
        tax = product.tax if (product.tax is not None and product.tax.active) else fallback
        variants = [v for v in product.variants if v.active or item_key(product.id, v.id) in include_keys]
        if product.variants:
            options = [(v, item_key(product.id, v.id)) for v in variants]
        else:
            options = [(None, item_key(product.id, None))]
        for variant, key in options:
            if not product.active and key not in include_keys:
                continue
            items.append(
                {
                    "key": key,
                    "label": f"{product.name} ({variant.label})" if variant else product.name,
                    "price": f"{unit_price_for(product, variant):f}",
                    "tax_id": tax.id if tax else "",
                    "type": product.product_type,
                }
            )
    return items


def parse_item_key(key: str):
    """'12:3' -> (Product 12, Variant 3). Returns (None, None) if invalid."""
    try:
        product_id, variant_id = (int(part) for part in (key or "").split(":"))
    except ValueError:
        return None, None
    product = db.session.get(Product, product_id)
    if product is None:
        return None, None
    variant = None
    if variant_id:
        variant = db.session.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product.id:
            return None, None
    elif product.variants:
        return None, None  # must pick one of the variants
    return product, variant


def read_lines(form, with_prices: bool):
    """Read the line editor rows from a submitted form.

    Returns (lines, rows): `lines` are validated dicts ready to save, `rows`
    keep what was typed (plus an error message) to re-render the editor.
    """
    data = form.data
    keys = data.getlist("line_item")
    qtys = data.getlist("line_qty")
    prices = data.getlist("line_price") if with_prices else []
    taxes = data.getlist("line_tax") if with_prices else []
    lines, rows = [], []

    for index, key in enumerate(keys):
        qty_raw = (qtys[index] if index < len(qtys) else "").strip()
        price_raw = (prices[index] if index < len(prices) else "").strip().replace(",", "")
        tax_raw = (taxes[index] if index < len(taxes) else "").strip()
        if not key and not qty_raw and not price_raw:
            continue  # blank row, ignore
        row = {"item": key, "qty": qty_raw, "price": price_raw, "tax": tax_raw, "error": None}
        rows.append(row)

        product, variant = parse_item_key(key)
        if product is None:
            row["error"] = "Choose a product."
            continue
        try:
            quantity = int(qty_raw)
            if not 1 <= quantity <= MAX_QTY:
                raise ValueError
        except ValueError:
            row["error"] = f"Quantity must be a whole number from 1 to {MAX_QTY:,}."
            continue

        line = {"product": product, "variant": variant, "quantity": quantity}
        if with_prices:
            try:
                unit_price = Decimal(price_raw) if price_raw else unit_price_for(product, variant)
                if not unit_price.is_finite() or unit_price < 0 or unit_price > MAX_PRICE:
                    raise InvalidOperation
            except InvalidOperation:
                row["error"] = "Unit price must be a number from 0 to 1,00,00,000."
                continue
            tax = None
            if tax_raw:
                try:
                    tax = db.session.get(Tax, int(tax_raw))
                except ValueError:
                    tax = None
                if tax is None:
                    row["error"] = "Choose a valid tax."
                    continue
            line["unit_price"] = money(unit_price)
            line["tax"] = tax
        lines.append(line)

    if not rows:
        form.error("lines", "Add at least one product line.")
    elif any(row["error"] for row in rows):
        form.error("lines", "Fix the highlighted lines.")
    return lines, rows


def rows_from_lines(lines, with_prices: bool) -> list[dict]:
    """Turn saved lines (template or subscription) into editor rows."""
    rows = []
    for line in lines:
        row = {"item": item_key(line.product_id, line.variant_id), "qty": str(line.quantity), "error": None}
        if with_prices:
            row["price"] = f"{money(line.unit_price):f}"
            row["tax"] = str(line.tax_id or "")
        rows.append(row)
    return rows
