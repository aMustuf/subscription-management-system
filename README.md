# Subscription Management System

One system to run plans, subscriptions and recurring billing for **Brightloop Software**, built for the Odoo x Make-A-Ton 9.0 hackathon (virtual round).

**Stack:** Python, Flask, SQLite (through SQLAlchemy), Bootstrap 5. All CSS, JS and fonts are stored in the repo, so the app runs offline.

## Run it (Windows)

```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open http://127.0.0.1:5000. The first start creates the database and demo data.

To wipe the database and load fresh demo data at any time:

```
.venv\Scripts\python seed.py
```

On macOS/Linux use `python3 -m venv .venv`, then `.venv/bin/python` in place of `.venv\Scripts\python`.

## Demo logins

| Role | Email | Password |
|---|---|---|
| Admin | admin@brightloop.com | Admin@12345 |
| Staff | staff@brightloop.com | Staff@12345 |
| Customer | customer@demo.com | Customer@123 |

## Features

### Accounts and roles
- Sign up with email and password. One account per email (unique, case-insensitive).
- Password rules from the brief, checked on the server and shown live while typing: more than 8 characters, an uppercase letter, a lowercase letter and a special character.
- Forgot password: a one-time reset link that expires in 1 hour is emailed to the registered address. Without email settings the message goes to the in-app Email outbox, and the demo shows the link on screen.
- Three roles. **Admin** controls everything. **Staff** handle day-to-day work with limited access. **Customers** see only their own subscriptions and invoices in a portal. Only the admin can add staff.

### Catalog (admin sets it up, staff can view)
- **Products** with name, type (service, goods, add-on), sales price, cost price, margin, tax and notes.
- **Variants** such as Brand: Odoo (+₹560). Final price = sales price + extra price.
- **Taxes**: rates set by the admin. A line uses its product's tax, otherwise the default tax.
- **Plans**: plan fee, billing period (daily, weekly, monthly, yearly), minimum quantity, availability dates, and per-plan rules: close automatically at expiry, close early, pause, renew.
- **Discounts** (admin only): percentage or fixed amount, minimum purchase or quantity, start and end dates, usage limit, for whole subscriptions or specific products.

### Sales
- **Customers**: staff add customers and email them a portal invite (a 7-day link to set their password).
- **Quotation templates**: a plan plus product lines and a validity period in days, ready to turn into a subscription.
- **Team**: only the admin can add staff. New members get an invite link and choose their own password.

## Project structure

```
app.py            Flask app: setup, navigation, error pages
config.py         Settings (database path, company details, email)
models.py         Every database table, relation and constraint
utils.py          Form validation, role checks, formatting, reset tokens
seed.py           Demo data (python seed.py resets the database)
routes/           One blueprint per area (auth, dashboard, portal, ...)
services/         Business logic (email, billing, ...)
templates/        Jinja pages; _macros.html holds shared form/status pieces
static/           CSS, JS, fonts and vendored Bootstrap
```

## Data integrity
- SQLite with foreign keys switched on, CHECK constraints (no negative prices, valid statuses, valid date ranges) and UNIQUE constraints (emails, document numbers).
- Every form is validated on the server. Errors appear next to the field and the form keeps what was typed.
- CSRF protection on every form.
