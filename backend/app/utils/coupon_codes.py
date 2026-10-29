"""Coupon-bundle code helpers: one normal form for a code, and the CSV reader
for the admin bulk import.

A bundle file is ONE column: the header is the first row, every row below
it is one code. Anything else is refused with FORMAT_HELP so an admin never
uploads a spreadsheet of names/amounts by mistake.
"""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass

CODE_PATTERN = re.compile(r"^[A-Z0-9_-]{4,32}$")
CODE_MIN, CODE_MAX = 4, 32

FORMAT_HELP = (
    "One column only. Put a header (for example Code) in the first row, then one coupon code on every row below it. "
    "Codes use letters, numbers, dash and underscore, 4 to 32 characters."
)

SAMPLE_CSV = "Code\r\nSUMMER-0001\r\nSUMMER-0002\r\nSUMMER-0003\r\n"

# Row problems (the reason shown beside an unselected row in the preview).
EMPTY = "Empty"
TOO_SHORT = "Too Short (Min 4)"
TOO_LONG = "Too Long (Max 32)"
BAD_CHARS = "Invalid Characters"
DUPLICATE_IN_FILE = "Duplicate In File"
EXISTS = "Already Exists"
EXTRA_COLUMNS = "Extra Columns"


def clean_cell(value: str) -> str:
    """Strip invisible junk (zero-width / BOM / control characters that are
    not whitespace) so a pasted code can't carry something unseen. Real
    whitespace inside the code is kept — it is then flagged as an invalid
    character instead of being silently glued together."""
    return "".join(
        ch for ch in value
        if ch.isspace() or not unicodedata.category(ch).startswith("C")
    )


def normalise_code(raw: str | None) -> str:
    """Trimmed, invisible characters removed, upper-case — the ONE form a
    code is stored and matched in."""
    cleaned = clean_cell(raw or "").strip()
    if not cleaned.isascii():
        # Never fold look-alikes into real codes: Unicode upper-casing turns
        # "ı" into "I" and "ſ" into "S", and a Cyrillic "С" is not a "C".
        # A code with any non-ASCII character matches nothing (the marker
        # also makes code_problem call it an invalid character).
        return "\ufffd" + cleaned
    return cleaned.upper()


def code_problem(code: str) -> str | None:
    """Why a normalised code can't be used, else None."""
    if not code:
        return EMPTY
    if len(code) < CODE_MIN:
        # Bad characters beat "too short" so the admin fixes the real cause.
        return BAD_CHARS if not re.fullmatch(r"[A-Z0-9_-]+", code) else TOO_SHORT
    if len(code) > CODE_MAX:
        return BAD_CHARS if not re.fullmatch(r"[A-Z0-9_-]+", code) else TOO_LONG
    return None if CODE_PATTERN.match(code) else BAD_CHARS


class CsvFormatError(ValueError):
    """The file isn't a one-column code list (message is shown to the admin)."""


@dataclass
class ParsedRow:
    row: int  # 1-based line number in the file (the header is row 1)
    code: str
    problem: str | None


def decode_upload(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvFormatError("The file must be a UTF-8 text CSV. Save it as CSV (UTF-8) and try again.") from exc
    if "\x00" in text:
        raise CsvFormatError("That doesn't look like a CSV text file.")
    return text


def parse_codes_csv(text: str, max_rows: int) -> list[ParsedRow]:
    """Rows below the header, each with its normalised code and a problem
    (duplicate-in-file included) when it can't be imported. Raises
    CsvFormatError for a file that isn't one column or is over `max_rows`."""
    reader = csv.reader(io.StringIO(text, newline=""))
    rows: list[ParsedRow] = []
    seen: set[str] = set()
    try:
        header = next(reader, None)
        if header is None or not any(cell.strip() for cell in header):
            raise CsvFormatError("The first row must be a header (for example Code). " + FORMAT_HELP)
        if any(cell.strip() for cell in header[1:]):
            raise CsvFormatError("The file has more than one column. " + FORMAT_HELP)
        for cells in reader:
            line = reader.line_num
            if not cells or not any(cell.strip() for cell in cells):
                continue  # blank line
            if any(cell.strip() for cell in cells[1:]):
                raise CsvFormatError(f"Row {line} has more than one column. " + FORMAT_HELP)
            if len(rows) >= max_rows:
                raise CsvFormatError(f"The file has more than {max_rows:,} codes — split it into smaller files.")
            code = normalise_code(cells[0])
            problem = code_problem(code)
            if problem is None:
                if code in seen:
                    problem = DUPLICATE_IN_FILE
                else:
                    seen.add(code)
            rows.append(ParsedRow(row=line, code=code, problem=problem))
    except csv.Error as exc:
        raise CsvFormatError(f"The CSV could not be read ({exc}). " + FORMAT_HELP) from exc
    if not rows:
        raise CsvFormatError("The file has no codes below the header row. " + FORMAT_HELP)
    return rows


def mask_code(code: str | None) -> str:
    """For logs: the first 3 characters only."""
    code = code or ""
    return f"{code[:3]}***" if code else ""
