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

# Layout T: activity-line blocks, mimicking PyMuPDF's vertical text output --
# including page-header noise, a foreign-currency block, a negative payment,
# an interest line with no reference number, and the closing total.
SAMPLE_T = """\
RBC Royal Bank
STATEMENT FROM OCT 18 TO NOV 15, 2022

TRANSACTION POSTING
ACTIVITY DESCRIPTION
AMOUNT ($)
DATE
DATE
OCT 19
OCT 20
STREAMFLIX.COM 800-555-0199
10000000000000000000001
$9.99
OCT 23
OCT 24
EXAMPLE MERCHANT SRL RM RM
10000000000000000000002
Foreign Currency-EUR 20.00
Exchange rate-1.385370
$27.71

RBC Cash Back Mastercard
ALEX DOE 4510 12** **** 3456
STATEMENT FROM OCT 18 TO NOV 15, 2022
2 OF 4
4510 12** **** 3456 - PRIMARY (continued)
TRANSACTION POSTING
ACTIVITY DESCRIPTION
AMOUNT ($)
DATE
DATE
NOV 08
NOV 09
PAYMENT - THANK YOU / PAIEMENT - MERCI
10000000000000000000003
-$25.00
NOV 15
NOV 15
PURCHASE INTEREST 19.99%
$40.00
TOTAL ACCOUNT BALANCE
$3,052.70
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


# --- Transaction extraction -------------------------------------------------

def test_extract_transactions_basic():
    txns = rbc.extract_transactions(SAMPLE_T, statement_date=date(2022, 11, 15))
    assert len(txns) == 4

    stream = txns[0]
    assert stream.transaction_date == date(2022, 10, 19)
    assert stream.posting_date == date(2022, 10, 20)
    assert stream.description == "STREAMFLIX.COM 800-555-0199"
    assert stream.amount == Decimal("9.99")

    # Foreign-currency block: reference + FX lines are skipped, amount is read.
    assert txns[1].description == "EXAMPLE MERCHANT SRL RM RM"
    assert txns[1].amount == Decimal("27.71")

    # Payment is negative.
    payment = txns[2]
    assert payment.amount == Decimal("-25.00")
    assert "PAYMENT" in payment.description

    # Interest line has no reference number but is still captured.
    assert txns[3].description == "PURCHASE INTEREST 19.99%"
    assert txns[3].amount == Decimal("40.00")

    # The "TOTAL ACCOUNT BALANCE" line is not a transaction.
    assert all("TOTAL ACCOUNT BALANCE" not in t.description for t in txns)


def test_transactions_reconcile_to_balance():
    s = rbc.extract_statement(SAMPLE_T)
    activity = sum((t.amount for t in s.transactions), Decimal(0))
    # previous balance is absent in this sample, but the activity total should
    # equal the printed new balance minus previous (here: just the activity).
    assert activity == Decimal("52.70")  # 9.99 + 27.71 - 25.00 + 40.00


def test_transaction_year_inference_across_year_boundary():
    # Statement closes in January; December activity belongs to the prior year.
    text = (
        "STATEMENT FROM DEC 18 TO JAN 15, 2023\n"
        "DEC 28\nDEC 29\nSOME STORE TORONTO ON\n12345678901234\n$10.00\n"
        "JAN 03\nJAN 04\nANOTHER STORE ON\n23456789012345\n$20.00\n"
        "TOTAL ACCOUNT BALANCE\n$30.00\n"
    )
    txns = rbc.extract_transactions(text, statement_date=date(2023, 1, 15))
    assert txns[0].transaction_date == date(2022, 12, 28)  # prior year
    assert txns[1].transaction_date == date(2023, 1, 3)


def test_extract_statement_attaches_transactions():
    s = rbc.extract_statement(SAMPLE_T, source_file="t.pdf")
    assert len(s.transactions) == 4


def test_write_transactions_csv(tmp_path):
    from autometron.finance.statements.process import (
        write_statement_transactions,
    )
    import csv as _csv

    s = rbc.extract_statement(SAMPLE_T, source_file="MasterCard 2022-11-15.pdf")
    written = write_statement_transactions([s], tmp_path)

    out = tmp_path / "MasterCard 2022-11-15_transactions.csv"
    assert out.exists()
    assert written[s.source_file] == out.name

    with out.open() as fh:
        rows = list(_csv.DictReader(fh))
    assert [c for c in rows[0]] == ["date", "posting_date", "description", "amount"]
    assert rows[0]["date"] == "2022-10-19"
    assert rows[0]["amount"] == "9.99"
    assert rows[2]["amount"] == "-25.00"


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
