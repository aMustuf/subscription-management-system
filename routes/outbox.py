"""Email outbox: every email the system sent (or saved, when email isn't set up)."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request

from extensions import db
from models import EmailLog
from services.mailer import email_enabled
from utils import current_page, staff_required

bp = Blueprint("outbox", __name__, url_prefix="/outbox")


@bp.route("/")
@staff_required
def index():
    kind = request.args.get("kind", "")
    query = EmailLog.query
    if kind:
        query = query.filter(EmailLog.kind == kind)
    pagination = query.order_by(EmailLog.created_at.desc(), EmailLog.id.desc()).paginate(
        page=current_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False
    )
    kinds = [k for (k,) in db.session.query(EmailLog.kind).distinct().order_by(EmailLog.kind)]
    return render_template("outbox/list.html", pagination=pagination, kind=kind, kinds=kinds,
                           mail_on=email_enabled())


@bp.route("/<int:email_id>")
@staff_required
def detail(email_id):
    email = db.session.get(EmailLog, email_id)
    if email is None:
        abort(404)
    return render_template("outbox/detail.html", email=email)
