"""Coupon-bundle CSV reading and code normalisation (pure — no database)."""
import pytest

from app.utils.coupon_codes import (
    BAD_CHARS, DUPLICATE_IN_FILE, EMPTY, TOO_LONG, TOO_SHORT, CsvFormatError, code_problem, decode_upload, mask_code,
    normalise_code, parse_codes_csv,
)

LIMIT = 100_000


def parse(text, limit=LIMIT):
    return parse_codes_csv(text, limit)


def test_header_is_skipped_and_codes_are_uppercased_and_trimmed():
    rows = parse("Code\r\n  abcd-1234  \r\nsave_10\r\n")
    assert [(r.code, r.problem) for r in rows] == [("ABCD-1234", None), ("SAVE_10", None)]
    assert [r.row for r in rows] == [2, 3]


def test_bom_crlf_blank_lines_and_quotes():
    raw = "﻿Code\r\n\r\n\"quoted1\"\r\n   \r\nplain99\r\n".encode("utf-8")
    rows = parse(decode_upload(raw))
    assert [r.code for r in rows] == ["QUOTED1", "PLAIN99"]


def test_trailing_empty_cells_are_fine_but_a_second_column_is_refused():
    assert [r.code for r in parse("Code,\nABCD1,\nABCD2,,\n")] == ["ABCD1", "ABCD2"]
    with pytest.raises(CsvFormatError, match="more than one column"):
        parse("Code\nABCD1,extra\n")
    with pytest.raises(CsvFormatError, match="more than one column"):
        parse("Code,Name\nABCD1,x\n")


def test_problems_are_flagged_per_row():
    rows = parse("Code\nabc\n" + "x" * 33 + "\nbad code\nOK-OK-1\nok-ok-1\n$$$$$\n​\n")
    got = {r.code: r.problem for r in rows}
    assert got["ABC"] == TOO_SHORT
    assert got["X" * 33] == TOO_LONG
    assert got["BAD CODE"] == BAD_CHARS
    assert got["$$$$$"] == BAD_CHARS
    assert got[""] == EMPTY  # only an invisible character
    assert [r.problem for r in rows if r.code == "OK-OK-1"] == [None, DUPLICATE_IN_FILE]


def test_invisible_characters_are_stripped_but_inner_whitespace_is_not_glued():
    assert normalise_code("AB​CD\x00-12") == "ABCD-12"
    assert code_problem(normalise_code("AB\tCD")) == BAD_CHARS
    assert normalise_code(None) == ""


def test_formula_like_cells_are_just_invalid_codes():
    rows = parse("Code\n=CMD|calc\n+SUM(1)\n-ABCD\n")
    assert [r.problem for r in rows[:2]] == [BAD_CHARS, BAD_CHARS]


def test_row_limit_and_empty_file():
    with pytest.raises(CsvFormatError, match="more than 3"):
        parse("Code\n" + "\n".join(f"CODE{i:04d}" for i in range(4)), limit=3)
    with pytest.raises(CsvFormatError, match="no codes"):
        parse("Code\n\n\n")
    with pytest.raises(CsvFormatError, match="header"):
        parse("")


def test_non_utf8_and_binary_are_refused():
    with pytest.raises(CsvFormatError, match="UTF-8"):
        decode_upload("Code\ncafé".encode("latin-1"))
    with pytest.raises(CsvFormatError):
        decode_upload(b"Code\n\x00\x01\x02")


def test_codes_are_only_logged_by_first_three_characters():
    assert mask_code("SECRET-CODE") == "SEC***"
    assert mask_code("") == ""
