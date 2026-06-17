"""RBC (Royal Bank of Canada) credit card statement extraction.

All RBC-specific knowledge -- the labels printed on the statement, the order
they appear in, and the quirks of RBC's "Calculating Your New Balance" section
-- is confined to this module. Everything else (PDF -> text, value parsing,
the data model, folder processing) is issuer-agnostic and lives elsewhere.

Extraction strategy: RBC prints each summary field as ``Label  value`` on a
single text line (e.g. ``Credit Limit  $5,000.00``). For each field we scan the
statement line by line, find the first line whose label matches, and pull the
value off that same line. A limited next-line fallback handles layouts where
the value wraps. Each field records the source line(s) it was read from.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from datetime import date

from .models import CreditCardStatement, STATEMENT_FIELDS, Transaction
from .parse import parse_amount, parse_date

logger = logging.getLogger(__name__)

# --- Value-matching regexes (within a single line) -------------------------

# Money amount, tolerant of currency symbol, thousands separators and the
# three negative conventions: leading "-", parentheses, trailing "CR".
_AMOUNT_RE = re.compile(r"\(?\s*-?\s*\$?\s*-?[\d,]+\.\d{2}\s*\)?(?:\s*CR)?")

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DATE_RE = re.compile(
    rf"(?:{_MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}}"  # March 15, 2022 / Mar 15 2022
    r"|\d{4}-\d{2}-\d{2}"                          # 2022-03-15
    r"|\d{1,2}/\d{1,2}/\d{4}",                     # 03/15/2022
    re.IGNORECASE,
)


# --- Field specifications ---------------------------------------------------
# Each spec: canonical name, ordered label patterns (most specific first),
# value kind, and optional "avoid" substrings that disqualify a candidate line
# (used to keep the interest *rate* from being read as interest *charged*).

class _FieldSpec:
    __slots__ = ("name", "labels", "kind", "avoid")

    def __init__(self, name: str, labels: list[str], kind: str,
                 avoid: Optional[list[str]] = None) -> None:
        self.name = name
        self.labels = [re.compile(p, re.IGNORECASE) for p in labels]
        self.kind = kind  # "amount" | "date"
        self.avoid = [re.compile(p, re.IGNORECASE) for p in (avoid or [])]


_SPECS: tuple[_FieldSpec, ...] = (
    # RBC MasterCard prints the statement date as a period, e.g.
    # "STATEMENT FROM MAR 17 TO APR 15, 2026" -- the closing (TO) date is the
    # statement date, and it is the only date with a year on that line, so the
    # date matcher picks it up correctly. Visa/other layouts use "Statement Date".
    _FieldSpec("statement_date",
               [r"statement\s+from\b.*\bto\b", r"statement\s+date",
                r"statement\s+period"], "date"),
    _FieldSpec("payment_due_date",
               [r"payment\s+due\s+date", r"due\s+date"], "date"),
    _FieldSpec("credit_limit",
               [r"credit\s+limit", r"total\s+credit\s+limit"], "amount",
               avoid=[r"available"]),
    _FieldSpec("previous_balance",
               [r"previous\s+(?:statement\s+|account\s+)?balance",
                r"previous\s+balance"], "amount"),
    _FieldSpec("payments",
               [r"payments\s*&\s*credits", r"payments\s+and\s+credits",
                r"payments\s*/\s*credits", r"payments\b"], "amount"),
    _FieldSpec("purchases",
               [r"purchases\s*,?\s*cash\s+advances\s*&\s*debits",
                r"new\s+purchases\s*(?:and|&)\s*debits",
                r"purchases\s*(?:and|&)\s*debits", r"purchases\b"], "amount"),
    _FieldSpec("interest_charged",
               [r"interest\s+charged", r"interest\s+charges",
                r"total\s+interest", r"interest\b"], "amount",
               avoid=[r"rate", r"%", r"interest[- ]free", r"grace", r"annual"]),
    _FieldSpec("fees",
               [r"fees\s+charged", r"total\s+fees", r"fees\b"], "amount",
               avoid=[r"annual\s+fee\s+rate"]),
    _FieldSpec("new_balance",
               [r"=\s*new\s+balance", r"new\s+balance\s*\(?total\)?",
                r"new\s+balance"], "amount"),
    _FieldSpec("minimum_payment",
               [r"minimum\s+payment", r"minimum\s+amount\s+due"], "amount"),
)


# --- Per-line value extraction ----------------------------------------------

def _amount_on_line(line: str) -> Optional[re.Match]:
    """Return the rightmost money match on ``line`` that is not a percentage."""
    best: Optional[re.Match] = None
    for m in _AMOUNT_RE.finditer(line):
        # Skip values immediately followed by "%" (e.g. an interest rate).
        if line[m.end():m.end() + 1] == "%":
            continue
        best = m
    return best


def _date_on_line(line: str) -> Optional[re.Match]:
    return _DATE_RE.search(line)


def _value_finder(kind: str) -> Callable[[str], Optional[re.Match]]:
    return _amount_on_line if kind == "amount" else _date_on_line


def _find_field(spec: _FieldSpec, lines: list[str]) -> Optional[tuple[object, str]]:
    """Locate ``spec`` in ``lines``; return ``(parsed_value, snippet)`` or None."""
    finder = _value_finder(spec.kind)
    parser = parse_amount if spec.kind == "amount" else parse_date

    # Pass 1: label and value on the same line.
    for label in spec.labels:
        for line in lines:
            if not label.search(line):
                continue
            if any(a.search(line) for a in spec.avoid):
                continue
            match = finder(line)
            if not match:
                continue
            value = parser(match.group(0))
            if value is not None:
                return value, line.strip()

    # Pass 2: value wrapped onto the next non-empty line.
    for label in spec.labels:
        for i, line in enumerate(lines):
            if not label.search(line) or any(a.search(line) for a in spec.avoid):
                continue
            if finder(line):  # already handled in pass 1
                continue
            for j in range(i + 1, min(i + 3, len(lines))):
                nxt = lines[j]
                if not nxt.strip():
                    continue
                match = finder(nxt)
                if match:
                    value = parser(match.group(0))
                    if value is not None:
                        snippet = f"{line.strip()} | {nxt.strip()}"
                        return value, snippet
                break
    return None


# --- Transaction (activity line) extraction ---------------------------------
#
# RBC prints each activity line as a vertical block of text lines:
#
#     OCT 19                      <- transaction date (bare "MON DD", no year)
#     OCT 20                      <- posting date
#     STREAMFLIX.COM 800-555-0199 <- description
#     10000000000000000000001     <- reference number (optional)
#     Foreign Currency-EUR 20.00  <- FX detail (optional)
#     Exchange rate-1.385370      <- FX detail (optional)
#     $9.99                       <- amount (negative for payments/credits)
#
# We anchor on two consecutive bare "MON DD" lines and read forward to the
# first stand-alone amount line. Page headers/footers between blocks are
# skipped naturally because they don't form a date pair.

_MONTH_ABBR = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_TXN_DATE_RE = re.compile(r"^([A-Z]{3,4})\s+(\d{1,2})$")
_TXN_AMOUNT_RE = re.compile(r"^\(?-?\$?[\d,]+\.\d{2}\)?(?:\s*CR)?$")
# Lines that sit between description and amount but are not the description.
_REF_RE = re.compile(r"^\d{6,}$")  # long reference numbers

# How far past the date pair we will look for the amount line.
_MAX_BLOCK_LINES = 8


def _txn_date(month_abbr: str, day: int,
              statement_date: Optional[date]) -> Optional[date]:
    """Resolve a bare ``MON DD`` to a full date using the statement's year.

    The statement period is ~1 month, so a transaction month later than the
    statement's closing month belongs to the previous calendar year (handles
    December activity on a January statement).
    """
    month = _MONTH_ABBR.get(month_abbr.upper())
    if month is None:
        return None
    if statement_date is None:
        return None
    year = statement_date.year
    if month > statement_date.month:
        year -= 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_transactions(text: str,
                        statement_date: Optional[date] = None) -> list[Transaction]:
    """Extract individual activity lines from RBC statement ``text``."""
    lines = [ln.strip() for ln in text.splitlines()]
    n = len(lines)
    transactions: list[Transaction] = []

    i = 0
    while i < n - 2:
        d1 = _TXN_DATE_RE.match(lines[i])
        d2 = _TXN_DATE_RE.match(lines[i + 1])
        if not (d1 and d2):
            i += 1
            continue

        description = lines[i + 2]
        # The description must be real text, not another amount/date line.
        if not description or _TXN_AMOUNT_RE.match(description):
            i += 1
            continue

        # Scan forward for the amount that closes this block.
        amount_idx: Optional[int] = None
        limit = min(i + 3 + _MAX_BLOCK_LINES, n)
        k = i + 3
        while k < limit:
            if _TXN_AMOUNT_RE.match(lines[k]):
                amount_idx = k
                break
            # A new date pair means this block had no amount; bail out.
            if (_TXN_DATE_RE.match(lines[k]) and k + 1 < n
                    and _TXN_DATE_RE.match(lines[k + 1])):
                break
            k += 1

        if amount_idx is None:
            i += 1
            continue

        transactions.append(Transaction(
            transaction_date=_txn_date(d1.group(1), int(d1.group(2)), statement_date),
            posting_date=_txn_date(d2.group(1), int(d2.group(2)), statement_date),
            description=description,
            amount=parse_amount(lines[amount_idx]),
        ))
        i = amount_idx + 1

    return transactions


# --- Public API -------------------------------------------------------------

def looks_like_rbc(text: str) -> bool:
    """Heuristic: does this statement text come from RBC?"""
    needles = ("royal bank of canada", "rbc royal bank", "rbc.com",
               "www.rbc.com", "rbcroyalbank")
    low = text.lower()
    return any(n in low for n in needles) or bool(re.search(r"\bRBC\b", text))


def extract_statement(text: str,
                      source_file: Optional[str] = None) -> CreditCardStatement:
    """Extract a :class:`CreditCardStatement` from RBC statement ``text``.

    Missing fields are left as ``None`` and logged at WARNING level.
    """
    lines = text.splitlines()
    values: dict[str, object] = {}
    sources: dict[str, str] = {}

    for spec in _SPECS:
        found = _find_field(spec, lines)
        if found is None:
            continue
        value, snippet = found
        values[spec.name] = value
        sources[spec.name] = snippet

    statement = CreditCardStatement(
        source_file=source_file,
        issuer="RBC",
        sources=sources,
        **values,
    )
    statement.transactions = extract_transactions(text, statement.statement_date)

    missing = statement.missing_fields()
    if missing:
        logger.warning(
            "RBC statement %s: could not extract %d field(s): %s",
            source_file or "<text>", len(missing), ", ".join(missing),
        )
    return statement


__all__ = ["extract_statement", "extract_transactions", "looks_like_rbc",
           "STATEMENT_FIELDS"]
