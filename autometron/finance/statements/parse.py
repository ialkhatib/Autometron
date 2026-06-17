"""Issuer-agnostic value parsing helpers (amounts and dates).

The *labels* used to find these values differ per issuer and live in the
issuer module (e.g. ``rbc.py``). The conversion of a matched string into a
``Decimal`` or ``date`` is generic and lives here.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger(__name__)

# A money amount as it appears on a statement, e.g. "$1,234.56", "-$12.00",
# "($45.67)" (parenthesised negative) or "78.90 CR" (trailing credit marker).
AMOUNT_RE = r"\(?-?\$?\s?-?[\d,]+\.\d{2}\)?(?:\s?CR)?"

_DATE_FORMATS = (
    "%B %d, %Y",   # March 15, 2022
    "%b %d, %Y",   # Mar 15, 2022
    "%b. %d, %Y",  # Mar. 15, 2022
    "%b %d %Y",    # Mar 15 2022
    "%Y-%m-%d",    # 2022-03-15
    "%m/%d/%Y",    # 03/15/2022
    "%d/%m/%Y",    # 15/03/2022 (last resort; ambiguous)
)


def parse_amount(raw: str) -> Optional[Decimal]:
    """Parse a monetary string into a ``Decimal``.

    Handles currency symbols, thousands separators, and the three negative
    conventions seen on statements: leading ``-``, parentheses, and a trailing
    ``CR`` credit marker. Returns ``None`` if the string is not a valid amount.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if re.search(r"\bCR\b", text, re.IGNORECASE):
        negative = True
    if "-" in text:
        negative = True

    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(raw: str) -> Optional[date]:
    """Parse a date string using the formats common to NA statements."""
    if not raw:
        return None
    text = raw.strip().rstrip(".")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.debug("Could not parse date from %r", raw)
    return None
