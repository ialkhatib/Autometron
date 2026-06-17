"""Data models for extracted credit card statements.

These models are issuer-agnostic: an issuer-specific extractor (e.g.
``autometron.finance.statements.rbc``) is responsible for populating them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

# The canonical set of fields we attempt to extract from every statement.
# Kept as a tuple so callers (CSV writer, tests) can iterate in a stable order.
STATEMENT_FIELDS: tuple[str, ...] = (
    "statement_date",
    "payment_due_date",
    "credit_limit",
    "previous_balance",
    "payments",
    "purchases",
    "interest_charged",
    "fees",
    "new_balance",
    "minimum_payment",
)

# Fields that hold monetary amounts (everything except the two dates).
MONEY_FIELDS: tuple[str, ...] = tuple(
    f for f in STATEMENT_FIELDS if not f.endswith("_date")
)


class Transaction(BaseModel):
    """A single posted transaction (activity line) on a statement.

    ``amount`` keeps the printed sign: purchases/interest/fees are positive,
    payments and credits are negative.
    """

    transaction_date: Optional[date] = None  # date the transaction occurred
    posting_date: Optional[date] = None       # date it posted to the account
    description: str = ""
    amount: Optional[Decimal] = None

    def to_csv_row(self) -> dict[str, str]:
        return {
            "date": (
                "" if self.transaction_date is None
                else self.transaction_date.isoformat()
            ),
            "posting_date": (
                "" if self.posting_date is None else self.posting_date.isoformat()
            ),
            "description": self.description,
            "amount": "" if self.amount is None else str(self.amount),
        }

    @staticmethod
    def csv_columns() -> list[str]:
        return ["date", "posting_date", "description", "amount"]


class CreditCardStatement(BaseModel):
    """Structured fields extracted from a single credit card statement PDF.

    Every extracted value may be ``None`` when the corresponding field could
    not be located in the statement text. For each field that *was* found, the
    raw text snippet it was extracted from is recorded in :attr:`sources` under
    the same field name, so extractions can be audited after the fact.
    """

    # Provenance.
    source_file: Optional[str] = None
    issuer: str = "RBC"

    # Dates.
    statement_date: Optional[date] = None
    payment_due_date: Optional[date] = None

    # Monetary fields.
    credit_limit: Optional[Decimal] = None
    previous_balance: Optional[Decimal] = None
    payments: Optional[Decimal] = None
    purchases: Optional[Decimal] = None
    interest_charged: Optional[Decimal] = None
    fees: Optional[Decimal] = None
    new_balance: Optional[Decimal] = None
    minimum_payment: Optional[Decimal] = None

    # field name -> raw source snippet the value was extracted from.
    sources: dict[str, str] = Field(default_factory=dict)

    # Individual activity lines for this statement.
    transactions: list[Transaction] = Field(default_factory=list)

    def missing_fields(self) -> list[str]:
        """Return the names of canonical fields that were not extracted."""
        return [f for f in STATEMENT_FIELDS if getattr(self, f) is None]

    def to_csv_row(self) -> dict[str, str]:
        """Flatten to a string dict suitable for ``csv.DictWriter``.

        Each extracted field gets a value column plus a ``<field>_source``
        column holding the snippet it came from.
        """
        row: dict[str, str] = {
            "source_file": self.source_file or "",
            "issuer": self.issuer,
        }
        for field in STATEMENT_FIELDS:
            value = getattr(self, field)
            row[field] = "" if value is None else str(value)
            row[f"{field}_source"] = self.sources.get(field, "")
        return row

    @staticmethod
    def csv_columns() -> list[str]:
        """Column order for the CSV produced by :func:`to_csv_row`."""
        columns = ["source_file", "issuer"]
        for field in STATEMENT_FIELDS:
            columns.append(field)
            columns.append(f"{field}_source")
        return columns
