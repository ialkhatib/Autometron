"""Batch-process a folder of statement PDFs into ``statements.csv``.

Only RBC statements are supported (per project scope). PDFs that do not look
like RBC statements are still processed with the RBC extractor but flagged with
a warning, so nothing is silently dropped.
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path

from . import rbc
from .extract import extract_text
from .models import CreditCardStatement, Transaction

logger = logging.getLogger(__name__)


def process_pdf(path: str | Path) -> CreditCardStatement:
    """Extract a single RBC statement PDF into a :class:`CreditCardStatement`."""
    path = Path(path)
    text = extract_text(path)
    if not text:
        logger.warning("No text extracted from %s; returning empty statement", path)
        return CreditCardStatement(source_file=path.name)
    if not rbc.looks_like_rbc(text):
        logger.warning(
            "%s does not look like an RBC statement; extracting anyway", path.name
        )
    return rbc.extract_statement(text, source_file=path.name)


def process_folder(folder: str | Path) -> list[CreditCardStatement]:
    """Process every ``*.pdf`` in ``folder`` (sorted by name)."""
    folder = Path(folder)
    pdfs = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF"))
    if not pdfs:
        logger.warning("No PDF files found in %s", folder)
    statements = []
    for pdf in pdfs:
        logger.info("Processing %s", pdf.name)
        statements.append(process_pdf(pdf))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract fields from RBC credit card statement PDFs."
    )
    parser.add_argument("folder", help="Folder containing statement PDFs")
    parser.add_argument(
        "-o", "--output", default="statements.csv",
        help="Output CSV path (default: statements.csv)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable info-level logging"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    statements = process_folder(args.folder)
    write_csv(statements, args.output)

    # Per-statement transaction CSVs go in a "transactions" folder next to the
    # master CSV (e.g. statements.csv -> transactions/<pdf-name>_transactions.csv).
    txn_dir = Path(args.output).resolve().parent / "transactions"
    write_statement_transactions(statements, txn_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
