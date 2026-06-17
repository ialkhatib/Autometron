# Autometron

Structured field extraction from financial statement PDFs.

## Credit card statements (RBC)

`autometron.finance.statements` extracts structured fields from **RBC** credit
card statement PDFs. It is intentionally *not* a general PDF reader — all
issuer-specific knowledge (labels, layout quirks) lives in
[`autometron/finance/statements/rbc.py`](autometron/finance/statements/rbc.py).
Adding another issuer means adding another module like it; the rest of the
pipeline is issuer-agnostic.

### Extracted fields

`statement_date`, `payment_due_date`, `credit_limit`, `previous_balance`,
`payments`, `purchases`, `interest_charged`, `fees`, `new_balance`,
`minimum_payment`.

Each is returned on a `CreditCardStatement` (a pydantic model). A field that
can't be found is `None` and a warning is logged. For every field that *was*
found, the raw text snippet it came from is recorded in `statement.sources`.

#### Transactions

Every statement's individual activity lines are also extracted into
`statement.transactions` — a list of `Transaction` objects with
`transaction_date`, `posting_date`, `description`, and `amount` (the printed
sign is kept: purchases/interest are positive, payments/credits negative).
Multi-line blocks, foreign-currency detail, reference numbers and repeated
page headers are handled. As a sanity check, on real statements the
transaction amounts sum exactly from the previous balance to the new balance.

### How it works

1. **Text extraction** (`extract.py`): PyMuPDF first, pdfplumber as a fallback.
   OCR (`pytesseract` + `pdf2image`) is attempted only if both yield no text.
2. **Field extraction** (`rbc.py`): regex-based, line-by-line. RBC prints each
   field as `Label  value`; a limited next-line fallback handles the stacked
   `Label\nvalue` layout used by RBC MasterCard statements. The statement date
   is read from the `STATEMENT FROM … TO <closing date>` period line.
3. **Output** (`process.py`): processes a folder of PDFs into a master
   `statements.csv` (a value column and a `<field>_source` snippet column per
   field), plus one transactions CSV per statement under `transactions/`
   (`<pdf-name>_transactions.csv`, columns: `date, posting_date, description,
   amount`).

### Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt
```

### Usage

CLI (writes `statements.csv`):

```bash
autometron-statements /path/to/folder-of-pdfs -o statements.csv -v
```

Library:

```python
from autometron.finance.statements import (
    process_folder, process_pdf, write_csv, write_statement_transactions,
)

statement = process_pdf("statement.pdf")
print(statement.new_balance, statement.sources["new_balance"])
for txn in statement.transactions:
    print(txn.transaction_date, txn.amount, txn.description)

statements = process_folder("folder/")
write_csv(statements, "statements.csv")                 # master summary
write_statement_transactions(statements, "transactions")  # one CSV per statement
```

### Tests

Tests use sample *extracted-text* strings (no real PDFs):

```bash
pytest
```
