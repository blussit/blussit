"""Spreadsheet-formula neutralising for the finance CSV export.

A cell that starts with = + - @ (or a tab / carriage return / newline) is
read by Excel / Sheets as a formula. Free text an admin typed (a name, a
note) could therefore run something on whoever opens the export. Such cells
get a leading apostrophe, which spreadsheets show as plain text."""
import csv
import io
from typing import Iterable, Sequence

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r", "\n")


def csv_safe_cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_DANGEROUS):
        return "'" + text
    return text


def build_csv(header: Sequence[str], rows: Iterable[Sequence]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([csv_safe_cell(h) for h in header])
    for row in rows:
        writer.writerow([csv_safe_cell(c) for c in row])
    return buf.getvalue()
