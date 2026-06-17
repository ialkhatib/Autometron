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

### How it works

1. **Text extraction** (`extract.py`): PyMuPDF first, pdfplumber as a fallback.
   OCR (`pytesseract` + `pdf2image`) is attempted only if both yield no text.
2. **Field extraction** (`rbc.py`): regex-based, line-by-line. RBC prints each
   field as `Label  value`; a limited next-line fallback handles the stacked
   `Label\nvalue` layout used by RBC MasterCard statements. The statement date
   is read from the `STATEMENT FROM … TO <closing date>` period line.
3. **Output** (`process.py`): processes a folder of PDFs into `statements.csv`,
   with a value column and a `<field>_source` snippet column per field.

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
from autometron.finance.statements import process_folder, process_pdf, write_csv

statement = process_pdf("statement.pdf")
print(statement.new_balance, statement.sources["new_balance"])

write_csv(process_folder("folder/"), "statements.csv")
```

### Tests

Tests use sample *extracted-text* strings (no real PDFs):

```bash
pytest
```
