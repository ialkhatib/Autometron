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
3. **Output** (`process.py`): recursively searches a master folder for PDFs and
   produces:
   - a master `statements.csv` (a value column and a `<field>_source` snippet
     column per field),
   - one transactions CSV per statement under `transactions/`
     (`<pdf-name>_transactions.csv`, columns: `date, posting_date, description,
     amount`),
   - one combined `transactions.csv` with **all** transactions across every
     statement, ordered by statement date (columns: `statement_date,
     source_file, date, posting_date, description, amount`).

   **Not every PDF is a statement.** PDFs that aren't recognised as RBC credit
   card statements (receipts, manuals, marketing, other issuers) are skipped,
   and read/parse errors on a single file never abort the batch.

   **No double counting.** The combined `transactions.csv` de-duplicates whole
   statements keyed on `(statement_date, previous_balance, new_balance)`, so the
   same statement appearing twice (a copy in another subfolder, or a re-download
   under a different name) is counted once. Genuinely identical transactions
   *within* a single statement are kept — they're distinct real charges.

### Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt
```

### Usage

CLI — recursively scans a master folder and writes `statements.csv`, a combined
`transactions.csv`, and per-statement CSVs under `transactions/`:

```bash
autometron-statements /path/to/master-folder -o statements.csv -v
# options: --no-recursive (top level only), -t/--transactions-output PATH
```

Library:

```python
from autometron.finance.statements import (
    process_folder, write_csv, write_statement_transactions,
    write_all_transactions_csv,
)

# Recursively find statement PDFs in a master folder (non-statements skipped).
statements = process_folder("master/", recursive=True)

write_csv(statements, "statements.csv")                    # master summary
write_statement_transactions(statements, "transactions")   # one CSV per statement
write_all_transactions_csv(statements, "transactions.csv") # combined, deduped, ordered
```

### Tests

Tests use sample *extracted-text* strings (no real PDFs):

```bash
pytest
```
