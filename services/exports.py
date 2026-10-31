"""CSV downloads that open cleanly in Excel and Google Sheets."""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from flask import Response


def csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buffer = io.StringIO()
    buffer.write("﻿")  # byte-order mark so Excel reads names with accents and ₹ correctly
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([f"{value:.2f}" if isinstance(value, Decimal) else value for value in row])
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
