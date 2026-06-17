"""Tests for RBC statement extraction.

These use sample *extracted text* strings that mimic what PyMuPDF/pdfplumber
produce from real RBC statements. No real PDFs are read.
"""

from datetime import date
from decimal import Decimal

import pytest

from autometron.finance.statements import rbc
from autometron.finance.statements.models import CreditCardStatement
from autometron.finance.statements.parse import parse_amount, parse_date
from autometron.finance.statements.process import write_csv


# --- Sample extracted text --------------------------------------------------

# Layout A: tabular header box + "Calculating Your New Balance" section.
SAMPLE_A = """\
RBC Royal Bank
Visa Statement

Statement Date          March 15, 2022
Payment Due Date        April 9, 2022
Credit Limit            $5,000.00
Available Credit        $3,765.44
Minimum Payment         $10.00

Calculating Your New Balance
Previous Statement Balance        $1,500.00
Payments & Credits              - $1,500.00
Purchases & Debits                $1,234.56
Cash Advances                     $0.00
Interest                          $0.00
Fees                              $0.00
= New Balance                     $1,234.56

Annual Interest Rate              19.99%
"""

# Layout B: dotted leaders, parenthesised credit, colon labels, "Total" prefixes.
SAMPLE_B = """\
ROYAL BANK OF CANADA
RBC Cash Back Mastercard

Statement Date: Jan 5, 2023
Payment Due Date: Jan 28, 2023
Total Credit Limit: $10,000.00

Previous Balance .............. $2,000.00
Payments & Credits ............ ($2,050.00)
Purchases and Debits .......... $3,100.25
Interest Charged .............. $45.10
Fees .......................... $29.00
New Balance ................... $3,124.35
Minimum Payment ............... $100.00
"""

# Layout D: real RBC MasterCard shape -- stacked label/value lines and the
# statement date expressed as a "STATEMENT FROM ... TO ..." period.
SAMPLE_D = """\
RBC Royal Bank
STATEMENT FROM MAR 17 TO APR 15, 2026

PAYMENTS & INTEREST RATES
Minimum payment
$150.00
Payment due date
MAY 11, 2026
Credit limit
$12,000.00
Available credit
$8,000.00
Annual interest rates:
Purchases
25.99%

CALCULATING YOUR BALANCE
Previous Account Balance
$4,000.00
Payments & credits
-$50.00
Purchases & debits
$0.00
Interest
$75.00
Fees
$0.00
NEW BALANCE
$4,025.00
"""

# Layout C: most fields missing.
SAMPLE_C = """\
RBC Royal Bank
Statement Date 2024-02-29
New Balance $42.00
Some unrelated marketing text about rewards.
"""


def test_sample_a_full_extraction():
    s = rbc.extract_statement(SAMPLE_A, source_file="a.pdf")
    assert s.statement_date == date(2022, 3, 15)
    assert s.payment_due_date == date(2022, 4, 9)
    assert s.credit_limit == Decimal("5000.00")        # not Available Credit
    assert s.previous_balance == Decimal("1500.00")
    assert s.payments == Decimal("-1500.00")           # leading "- $"
    assert s.purchases == Decimal("1234.56")
    assert s.interest_charged == Decimal("0.00")       # not the 19.99% rate
    assert s.fees == Decimal("0.00")
    assert s.new_balance == Decimal("1234.56")
    assert s.minimum_payment == Decimal("10.00")
    assert s.missing_fields() == []


def test_sample_a_avoids_available_credit_and_rate():
    s = rbc.extract_statement(SAMPLE_A)
    # The 19.99% rate must not be captured as interest charged.
    assert s.interest_charged != Decimal("19.99")
    # Available Credit ($3,765.44) must not be captured as the credit limit.
    assert s.credit_limit != Decimal("3765.44")


def test_sample_b_negatives_and_leaders():
    s = rbc.extract_statement(SAMPLE_B, source_file="b.pdf")
    assert s.statement_date == date(2023, 1, 5)
    assert s.payment_due_date == date(2023, 1, 28)
    assert s.credit_limit == Decimal("10000.00")
    assert s.previous_balance == Decimal("2000.00")
    assert s.payments == Decimal("-2050.00")           # parenthesised credit
    assert s.purchases == Decimal("3100.25")
    assert s.interest_charged == Decimal("45.10")
    assert s.fees == Decimal("29.00")
    assert s.new_balance == Decimal("3124.35")
    assert s.minimum_payment == Decimal("100.00")


def test_sample_d_stacked_layout_and_period_date():
    s = rbc.extract_statement(SAMPLE_D, source_file="d.pdf")
    # Statement date is the closing ("TO") date of the period.
    assert s.statement_date == date(2026, 4, 15)
    assert s.payment_due_date == date(2026, 5, 11)
    assert s.credit_limit == Decimal("12000.00")   # not Available credit
    assert s.previous_balance == Decimal("4000.00")
    assert s.payments == Decimal("-50.00")
    assert s.purchases == Decimal("0.00")
    assert s.interest_charged == Decimal("75.00")  # not the 25.99% rate
    assert s.fees == Decimal("0.00")
    assert s.new_balance == Decimal("4025.00")
    assert s.minimum_payment == Decimal("150.00")
    assert s.missing_fields() == []


def test_missing_fields_return_none_and_warn(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        s = rbc.extract_statement(SAMPLE_C, source_file="c.pdf")

    assert s.statement_date == date(2024, 2, 29)
    assert s.new_balance == Decimal("42.00")
    # Everything else is missing.
    assert s.credit_limit is None
    assert s.payments is None
    assert s.minimum_payment is None
    assert "could not extract" in caplog.text
    assert set(s.missing_fields()) == {
        "payment_due_date", "credit_limit", "previous_balance", "payments",
        "purchases", "interest_charged", "fees", "minimum_payment",
    }


def test_source_snippets_recorded():
    s = rbc.extract_statement(SAMPLE_B)
    assert "Interest Charged" in s.sources["interest_charged"]
    assert "$45.10" in s.sources["interest_charged"]
    # Missing fields have no snippet.
    assert "credit_limit" in s.sources
    assert "cash_advances" not in s.sources


def test_looks_like_rbc():
    assert rbc.looks_like_rbc(SAMPLE_A)
    assert rbc.looks_like_rbc("Statement from Royal Bank of Canada")
    assert not rbc.looks_like_rbc("Some TD Bank Visa statement text")


# --- Value parser unit tests ------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("$1,234.56", Decimal("1234.56")),
    ("1234.56", Decimal("1234.56")),
    ("- $500.00", Decimal("-500.00")),
    ("-$500.00", Decimal("-500.00")),
    ("($45.67)", Decimal("-45.67")),
    ("$500.00 CR", Decimal("-500.00")),
    ("$0.00", Decimal("0.00")),
    ("", None),
    ("N/A", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("March 15, 2022", date(2022, 3, 15)),
    ("Mar 15, 2022", date(2022, 3, 15)),
    ("Jan 5, 2023", date(2023, 1, 5)),
    ("2024-02-29", date(2024, 2, 29)),
    ("03/15/2022", date(2022, 3, 15)),
    ("not a date", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_cr_trailing_credit_marker():
    text = "RBC\nPayments & Credits   $500.00 CR\n"
    s = rbc.extract_statement(text)
    assert s.payments == Decimal("-500.00")


# --- CSV output -------------------------------------------------------------

def test_write_csv_roundtrip(tmp_path):
    statements = [
        rbc.extract_statement(SAMPLE_A, source_file="a.pdf"),
        rbc.extract_statement(SAMPLE_B, source_file="b.pdf"),
    ]
    out = tmp_path / "statements.csv"
    write_csv(statements, out)

    import csv as _csv

    with out.open() as fh:
        rows = list(_csv.DictReader(fh))

    assert len(rows) == 2
    assert rows[0]["source_file"] == "a.pdf"
    assert rows[0]["new_balance"] == "1234.56"
    assert "$1,234.56" in rows[0]["new_balance_source"]
    assert rows[1]["payments"] == "-2050.00"


def test_csv_columns_include_sources():
    cols = CreditCardStatement.csv_columns()
    assert "new_balance" in cols
    assert "new_balance_source" in cols
    assert cols[0] == "source_file"


def test_process_folder_with_monkeypatched_text(tmp_path, monkeypatch):
    from autometron.finance.statements import process

    # Create placeholder PDF files; their *content* is supplied via monkeypatch.
    (tmp_path / "one.pdf").write_bytes(b"%PDF-1.4 placeholder")
    (tmp_path / "two.pdf").write_bytes(b"%PDF-1.4 placeholder")

    texts = {"one.pdf": SAMPLE_A, "two.pdf": SAMPLE_B}
    monkeypatch.setattr(process, "extract_text",
                        lambda p: texts[__import__("pathlib").Path(p).name])

    statements = process.process_folder(tmp_path)
    assert len(statements) == 2
    assert statements[0].source_file == "one.pdf"
    assert statements[0].new_balance == Decimal("1234.56")
    assert statements[1].minimum_payment == Decimal("100.00")
