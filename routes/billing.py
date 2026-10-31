"""Billing day: create invoices for every subscription that is due, in one click."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, flash, render_template, request
from flask_login import current_user

from services.billing import due_preview, run_billing
from utils import Form, staff_required

bp = Blueprint("billing", __name__, url_prefix="/billing")


@bp.route("/", methods=["GET", "POST"])
@staff_required
def run():
    today = date.today()
    result = None
    form = Form(request.form if request.method == "POST" else None, initial={"run_date": today})
    if form.submitted:
        run_date = form.date("run_date", "Billing date")
        if run_date and run_date > today:
            form.error("run_date", "You can bill up to today, not future dates.")
        if form.ok:
            result = run_billing(run_date, user=current_user, auto_confirm=form.boolean("auto_confirm"))
            if result["count"]:
                flash(f"{result['count']:,} invoice(s) created for {result['subscriptions']:,} subscription(s); "
                      f"{result['discounted']:,} got a discount automatically.", "success")
            else:
                flash("Nothing was due. Every active subscription is billed up to date.", "info")
    preview = due_preview(today)
    return render_template("billing/run.html", form=form, result=result, preview=preview)
