"""Credit card statement field extraction.

Currently RBC-only. The public entry points are:

- :func:`process_pdf` / :func:`process_folder` -- PDFs in, statements out
- :func:`write_csv` -- statements out to ``statements.csv``
- :class:`CreditCardStatement` -- the structured result model
- :mod:`rbc` -- the RBC-specific extractor (``rbc.extract_statement``)
"""

from .models import CreditCardStatement, STATEMENT_FIELDS, Transaction
from .process import (
    process_folder,
    process_pdf,
    write_csv,
    write_statement_transactions,
    write_transactions_csv,
)

__all__ = [
    "CreditCardStatement",
    "Transaction",
    "STATEMENT_FIELDS",
    "process_folder",
    "process_pdf",
    "write_csv",
    "write_transactions_csv",
    "write_statement_transactions",
]
