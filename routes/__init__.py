"""Web routes, split into one Flask blueprint per area of the app."""
import importlib

# Each module in routes/ defines `bp`. Modules that don't exist yet are skipped.
MODULES = [
    "auth",
    "main",
    "portal",
    "customers",
    "team",
    "products",
    "taxes",
    "plans",
    "discounts",
    "quotes",
    "subscriptions",
    "invoices",
    "payments",
    "billing",
    "reports",
    "outbox",
]


def all_blueprints():
    blueprints = []
    for name in MODULES:
        try:
            module = importlib.import_module(f"routes.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"routes.{name}":
                continue
            raise
        blueprints.append(module.bp)
    return blueprints
