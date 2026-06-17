# Autometron

**Turn a pile of RBC credit card statement PDFs into clean, structured data.**

Point Autometron at a folder of statements and it gives you back a tidy summary
of each statement *and* a single, de-duplicated ledger of every transaction —
ready for a spreadsheet, a budget, or a database.

```bash
# Replace path/to/statements with your own RBC statements folder.
autometron-statements path/to/statements -o statements.csv
# → statements.csv          one row per statement (balances, dates, limits…)
# → transactions.csv        every transaction, deduped, ordered by date
# → transactions/*.csv      one file per statement
```

It is intentionally **not** a general PDF reader. It knows one thing well — RBC
credit card statements — and all of that issuer-specific knowledge lives in a
single module, [`autometron/finance/statements/rbc.py`](autometron/finance/statements/rbc.py).
Adding another bank later means adding another module like it; nothing else changes.

---

## Why

Statement PDFs are designed to be *read*, not *queried*. The numbers you care
about — what you spent, what recurring charges you have, how interest is adding
up month over month — are locked in page layouts. Autometron extracts them into
plain CSV/objects so you can answer those questions in a few lines of code.

## Features

- 📄 **Statement summary** — `statement_date`, `payment_due_date`,
  `credit_limit`, `previous_balance`, `payments`, `purchases`,
  `interest_charged`, `fees`, `new_balance`, `minimum_payment`.
- 🧾 **Per-transaction detail** — date, posting date, description, amount (signed).
- 🗂️ **One combined ledger** — all transactions across all statements, ordered by
  statement date, **with no double-counting**.
- 🔍 **Recursive search** of a master folder, and it **skips PDFs that aren't
  statements** (receipts, manuals, other banks).
- 🧮 **Auditable** — every extracted field keeps the exact text snippet it came
  from. On real statements the transactions reconcile *previous → new balance* to
  the penny.
- 🛟 **Robust** — a missing field becomes `None` + a warning; a single unreadable
  PDF never aborts the batch.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt
```

## 30-second quick start

```python
from autometron.finance.statements import process_folder

statements = process_folder("path/to/statements", recursive=True)

s = statements[0]
print(s.statement_date, s.new_balance, "·", len(s.transactions), "transactions")
# 2022-11-15 3052.70 · 4 transactions
```

## Output files

| File | What's in it | Columns |
|------|--------------|---------|
| `statements.csv` | one row per statement | each field + a `<field>_source` snippet |
| `transactions.csv` | **all** transactions, deduped, ordered by statement date | `statement_date, source_file, date, posting_date, description, amount` |
| `transactions/<pdf>_transactions.csv` | one statement's transactions | `date, posting_date, description, amount` |

---

## Use cases

### 1. Build `transactions.csv` from every PDF in a folder

`path/to/statements` is a placeholder — point it at your own master RBC folder
(the PDFs can be in nested subfolders).

```bash
# CLI: scan the folder recursively and write a combined transactions.csv
autometron-statements path/to/statements -o transactions.csv

# If your folder name has spaces, quote it:
autometron-statements "path/to/My Statements" -o transactions.csv
```

This recursively finds every statement PDF, skips anything that isn't an RBC
statement, and writes one `transactions.csv` ordered by statement date (a
re-downloaded duplicate of the same statement is counted once). The resulting
file looks like:

```text
statement_date,source_file,date,posting_date,description,amount
2022-11-15,MasterCard 2022-11-15.pdf,2022-10-19,2022-10-20,STREAMFLIX.COM 800-555-0199,9.99
2022-11-15,MasterCard 2022-11-15.pdf,2022-10-21,2022-10-21,MEGASTORE.CA MEMBERSHIP,9.99
...
2022-11-15,MasterCard 2022-11-15.pdf,2022-11-15,2022-11-15,PURCHASE INTEREST 19.99%,40.00
2026-04-15,MasterCard 2026-04-15.pdf,2026-04-01,2026-04-01,PAYMENT - THANK YOU,-50.00
```

Equivalent in Python (just the combined `transactions.csv`, nothing else):

```python
from autometron.finance.statements import process_folder, write_all_transactions_csv

statements = process_folder("path/to/statements", recursive=True)
write_all_transactions_csv(statements, "transactions.csv")
```

### 2. How much did I spend vs. pay in a month?

```python
from decimal import Decimal
from autometron.finance.statements import process_pdf

s = process_pdf("MasterCard 2022-11-15.pdf")
spent  = sum((t.amount for t in s.transactions if t.amount > 0), Decimal(0))
paid   = sum((-t.amount for t in s.transactions if t.amount < 0), Decimal(0))
print(f"Spent ${spent}, paid ${paid}")     # Spent $77.70, paid $25.00
```

### 3. Find recurring subscriptions across all statements

```python
from collections import Counter
from autometron.finance.statements import process_folder

statements = process_folder("path/to/statements", recursive=True)
merchants = Counter(
    t.description.split("  ")[0][:24].strip()
    for s in statements for t in s.transactions if t.amount > 0
)
for name, times in merchants.most_common(5):
    print(f"{times:>3}×  {name}")     # 12×  STREAMFLIX.COM …
```

### 4. Spend by merchant (simple categorization)

```python
from decimal import Decimal
from autometron.finance.statements import process_folder, write_all_transactions_csv

statements = process_folder("path/to/statements", recursive=True)
totals = {}
for s in statements:
    for t in s.transactions:
        if t.amount and t.amount > 0:
            key = "UBER" if "UBER" in t.description else t.description[:20]
            totals[key] = totals.get(key, Decimal(0)) + t.amount

for k, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:5]:
    print(f"${v:>9}  {k}")

# Or just dump everything to a spreadsheet:
write_all_transactions_csv(statements, "transactions.csv")
```

### 5. Reconcile a statement (trust, but verify)

```python
from decimal import Decimal
from autometron.finance.statements import process_pdf

s = process_pdf("MasterCard 2022-11-15.pdf")
activity = sum((t.amount for t in s.transactions), Decimal(0))
assert s.previous_balance + activity == s.new_balance   # 3000.00 + 52.70 == 3052.70
```

### 6. Audit where a number came from

```python
s = process_pdf("MasterCard 2026-04-15.pdf")
print(s.new_balance)                  # 4025.00
print(s.sources["new_balance"])       # 'NEW BALANCE | $4,025.00'   ← the source text
print(s.missing_fields())             # [] (everything was found)
```

### 7. Hand it a messy folder — it sorts statements from the rest

```python
from autometron.finance.statements import process_folder

# Folder has statements, receipts, an air-fryer manual, an RBC marketing flyer…
statements = process_folder("~/Downloads", recursive=True)
# Only real RBC credit card statements come back; the rest are skipped + logged.
```

---

## Data model

```python
class CreditCardStatement(BaseModel):
    source_file: str | None
    issuer: str = "RBC"
    statement_date: date | None
    payment_due_date: date | None
    credit_limit: Decimal | None
    previous_balance: Decimal | None
    payments: Decimal | None
    purchases: Decimal | None
    interest_charged: Decimal | None
    fees: Decimal | None
    new_balance: Decimal | None
    minimum_payment: Decimal | None
    sources: dict[str, str]            # field name → exact source snippet
    transactions: list[Transaction]

    def missing_fields(self) -> list[str]: ...

class Transaction(BaseModel):
    transaction_date: date | None
    posting_date: date | None
    description: str
    amount: Decimal | None             # signed: charges +, payments/credits −
```

## CLI reference

```text
autometron-statements FOLDER [options]

  FOLDER                       master folder to scan for statement PDFs
  -o, --output PATH            master summary CSV   (default: statements.csv)
  -t, --transactions-output P  combined transactions CSV
                               (default: transactions.csv next to --output)
  --recursive / --no-recursive search subfolders (default: recursive)
  -v, --verbose                info-level logging
```

## How it works

1. **Text extraction** (`extract.py`) — PyMuPDF first, pdfplumber as a fallback;
   OCR (`pytesseract` + `pdf2image`) only if both yield no text.
2. **Classification** — a PDF must show an RBC brand signal *and* statement
   structure (`new balance`, `payment due date`, …) to be treated as a statement.
3. **Field extraction** (`rbc.py`) — regex, line-by-line. RBC prints each field
   as `Label  value` (or `Label` then `value` on the next line, as RBC MasterCard
   does). The statement date is read from the `STATEMENT FROM … TO <closing date>`
   period line.
4. **Transactions** (`rbc.py`) — anchors on two bare `MON DD` date lines, reads
   through the description / reference number / foreign-currency lines to the
   amount, and infers the year from the statement period (handles Dec→Jan).
5. **Output** (`process.py`) — master summary, per-statement CSVs, and one
   combined `transactions.csv` that de-duplicates whole statements keyed on
   `(statement_date, previous_balance, new_balance)`. Identical transactions
   *within* a statement are kept — they're distinct real charges.

## Extending to another issuer

The pipeline (`extract.py`, `models.py`, `parse.py`, `process.py`) is
issuer-agnostic. To add, say, TD: create `td.py` exposing `extract_statement`,
`extract_transactions`, and `looks_like_*_statement`, mirroring `rbc.py`, then
route to it based on the detected issuer. The data model and CSV output are reused
as-is.

## Limitations

- Tuned to the RBC MasterCard layout seen in real statements. A different RBC
  template (or RBC Visa) may need small label/regex additions in `rbc.py`; the
  extractor degrades gracefully (warns, extracts what it can) rather than crashing.
- No OCR by default — scanned/image-only statements need the optional `ocr` extras.
- Always sanity-check a new template with the reconciliation in use case 5.

## Tests

39 tests, driven entirely by sample *extracted-text* strings (no real PDFs):

```bash
pytest
```
