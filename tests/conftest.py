"""Shared test setup: a fresh in-memory database with the demo data for every test."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["AUTO_SEED"] = "0"

from app import create_app  # noqa: E402
from config import TestConfig  # noqa: E402
from extensions import db  # noqa: E402
from seed import seed_demo_data  # noqa: E402


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_demo_data()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def login(client):
    def do_login(email, password):
        client.post("/logout")
        return client.post("/login", data={"email": email, "password": password}, follow_redirects=True)

    return do_login


ADMIN = ("admin@brightloop.com", "Admin@12345")
STAFF = ("staff@brightloop.com", "Staff@12345")
CUSTOMER = ("customer@demo.com", "Customer@123")
