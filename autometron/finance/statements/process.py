"""Batch-process a folder of statement PDFs into ``statements.csv``.

Only RBC statements are supported (per project scope). PDFs that do not look
like RBC statements are still processed with the RBC extractor but flagged with
a warning, so nothing is silently dropped.
"""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import date
from pathlib import Path

from . import rbc
from .extract import extract_text
from .models import CreditCardStatement, Transaction

logger = logging.getLogger(__name__)


def find_pdfs(folder: str | Path, recursive: bool = False) -> list[Path]:
    """Return PDF files in ``folder``, recursing into subfolders if asked.

    Matching is case-insensitive (``.pdf``/``.PDF``) and de-duplicated by
    resolved path so the same file is never returned twice.
    """
    folder = Path(folder)
    globber = folder.rglob if recursive else folder.glob
    found: dict[Path, Path] = {}
    for path in globber("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            found[path.resolve()] = path
    return sorted(found.values())


def process_pdf(path: str | Path,
                require_statement: bool = False) -> CreditCardStatement | None:
    """Extract a single PDF into a :class:`CreditCardStatement`.

    Not every PDF is a statement. When ``require_statement`` is True, a PDF that
    is not recognised as an RBC credit card statement (or yields no text) is
    skipped and ``None`` is returned. When False (the default for direct,
    single-file calls), extraction is attempted regardless.

    Text/parse errors are caught so a single bad file never aborts a batch.
    """
    path = Path(path)
    try:
        text = extract_text(path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not read %s: %s; skipping", path.name, exc)
        return None

    if not text:
        if require_statement:
            logger.info("Skipping %s: no extractable text", path.name)
            return None
        logger.warning("No text extracted from %s; returning empty statement", path)
        return CreditCardStatement(source_file=path.name)

    if not rbc.looks_like_rbc_statement(text):
        if require_statement:
            logger.info(
                "Skipping %s: not recognised as an RBC credit card statement",
                path.name,
            )
            return None
        logger.warning(
            "%s does not look like an RBC statement; extracting anyway", path.name
        )

    try:
        return rbc.extract_statement(text, source_file=path.name)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to extract fields from %s: %s; skipping",
                       path.name, exc)
        return None


def process_folder(folder: str | Path,
                   recursive: bool = False) -> list[CreditCardStatement]:
    """Process the statement PDFs in ``folder`` (and subfolders if
    ``recursive``).

    PDFs that are not RBC credit card statements are skipped, so a mixed
    "master" folder of unrelated PDFs is handled safely.
    """
    pdfs = find_pdfs(folder, recursive=recursive)
    if not pdfs:
        logger.warning("No PDF files found in %s", folder)
    statements = []
    skipped = 0
    for pdf in pdfs:
        statement = process_pdf(pdf, require_statement=True)
        if statement is None:
            skipped += 1
            continue
        logger.info("Processed statement %s", pdf.name)
        statements.append(statement)
    logger.info("Found %d statement(s); skipped %d non-statement PDF(s)",
                len(statements), skipped)
    return statements


def write_csv(statements: list[CreditCardStatement], out_path: str | Path) -> None:
    """Write the master statement summary CSV to ``out_path``."""
    out_path = Path(out_path)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CreditCardStatement.csv_columns())
        writer.writeheader()
        for statement in statements:
            writer.writerow(statement.to_csv_row())
    logger.info("Wrote %d statement(s) to %s", len(statements), out_path)


def write_transactions_csv(transactions: list[Transaction],
                           out_path: str | Path) -> None:
    """Write one statement's transactions (date, posting_date, description,
    amount) to ``out_path``."""
    out_path = Path(out_path)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=Transaction.csv_columns())
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn.to_csv_row())
    logger.info("Wrote %d transaction(s) to %s", len(transactions), out_path)


def _transactions_filename(statement: CreditCardStatement) -> str:
    """Stable per-statement transactions filename derived from the source PDF."""
    stem = Path(statement.source_file).stem if statement.source_file else "statement"
    return f"{stem}_transactions.csv"


def write_statement_transactions(statements: list[CreditCardStatement],
                                 out_dir: str | Path) -> dict[str, str]:
    """Write a transactions CSV per statement into ``out_dir``.

    Returns a mapping of source file -> transactions CSV filename.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for statement in statements:
        name = _transactions_filename(statement)
        write_transactions_csv(statement.transactions, out_dir / name)
        if not statement.transactions:
            logger.warning("No transactions extracted for %s",
                           statement.source_file or "<unknown>")
        written[statement.source_file or name] = name
    return written


# Columns for the combined, all-statements transactions CSV. The statement
# context (date + source file) is added so rows stay traceable once merged.
_COMBINED_COLUMNS = [
    "statement_date", "source_file", "date", "posting_date", "description", "amount",
]


def _statement_key(statement: CreditCardStatement) -> tuple:
    """A key that uniquely identifies a statement, for de-duplication.

    ``(statement_date, previous_balance, new_balance)`` pins a specific
    statement of a specific account, so copies of the same statement (or a
    re-download under a different filename) collapse to one. When the date is
    missing we fall back to the source filename rather than risk dropping it.
    """
    if statement.statement_date is not None:
        return ("stmt", statement.statement_date,
                str(statement.previous_balance), str(statement.new_balance))
    return ("file", statement.source_file)


def deduplicate_statements(
    statements: list[CreditCardStatement],
) -> list[CreditCardStatement]:
    """Drop statements that are duplicates of one already seen (see
    :func:`_statement_key`). Order is preserved."""
    seen: set[tuple] = set()
    unique: list[CreditCardStatement] = []
    for statement in statements:
        key = _statement_key(statement)
        if key in seen:
            logger.warning(
                "Skipping duplicate statement %s (already counted)",
                statement.source_file or "<unknown>",
            )
            continue
        seen.add(key)
        unique.append(statement)
    return unique


def combined_transaction_rows(
    statements: list[CreditCardStatement],
) -> list[dict[str, str]]:
    """De-duplicate statements, then return every transaction as a CSV row,
    ordered by statement date (then transaction date, then statement order).

    Identical transactions *within* the same statement are preserved -- they
    are distinct real charges, not double counting.
    """
    unique = deduplicate_statements(statements)

    # (sort key, row) tuples. None dates sort last via date.max.
    indexed: list[tuple[tuple, dict[str, str]]] = []
    for statement in unique:
        stmt_date = statement.statement_date
        stmt_date_str = stmt_date.isoformat() if stmt_date else ""
        for order, txn in enumerate(statement.transactions):
            row = txn.to_csv_row()
            row = {
                "statement_date": stmt_date_str,
                "source_file": statement.source_file or "",
                **row,
            }
            sort_key = (
                stmt_date or date.max,
                txn.transaction_date or date.max,
                order,
            )
            indexed.append((sort_key, row))

    indexed.sort(key=lambda item: item[0])
    return [row for _, row in indexed]


def write_all_transactions_csv(statements: list[CreditCardStatement],
                               out_path: str | Path) -> int:
    """Write one combined, de-duplicated, date-ordered ``transactions.csv`` for
    all statements. Returns the number of transaction rows written."""
    out_path = Path(out_path)
    rows = combined_transaction_rows(statements)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COMBINED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d combined transaction(s) to %s", len(rows), out_path)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract fields from RBC credit card statement PDFs."
    )
    parser.add_argument("folder", help="Master folder containing statement PDFs")
    parser.add_argument(
        "-o", "--output", default="statements.csv",
        help="Master summary CSV path (default: statements.csv)",
    )
    parser.add_argument(
        "-t", "--transactions-output", default=None,
        help="Combined transactions CSV path "
             "(default: transactions.csv next to --output)",
    )
    parser.add_argument(
        "-r", "--recursive", action=argparse.BooleanOptionalAction, default=True,
        help="Search subfolders of the master folder (default: on)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable info-level logging"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    statements = process_folder(args.folder, recursive=args.recursive)
    write_csv(statements, args.output)

    out_parent = Path(args.output).resolve().parent

    # Per-statement transaction CSVs go in a "transactions" folder next to the
    # master CSV (e.g. statements.csv -> transactions/<pdf-name>_transactions.csv).
    write_statement_transactions(statements, out_parent / "transactions")

    # One combined, de-duplicated, date-ordered transactions.csv.
    txn_csv = args.transactions_output or (out_parent / "transactions.csv")
    write_all_transactions_csv(statements, txn_csv)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
