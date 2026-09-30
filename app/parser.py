import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import Invoice, LineItem

LABELS = {
    "invoice_number": ["Invoice number", "Invoice no", "Invoice #"],
    "invoice_date": ["Invoice date", "Date"],
    "due_date": ["Due date"],
    "vendor": ["Vendor", "Seller", "From"],
    "customer": ["Customer", "Bill to"],
    "currency": ["Currency"],
    "subtotal": ["Subtotal", "Sub-total"],
    "tax": ["Tax", "VAT"],
    "total": ["Total", "Amount due", "Grand total"],
}


def money(raw: str) -> Decimal | None:
    # Ambiguous comma-only decimals are deliberately rejected.
    value = re.sub(r"[A-Z$€£\s]", "", raw.strip())
    if "," in value:
        if not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", value):
            return None
        value = value.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", value):
        return None
    if len(value.split(".")[0].lstrip("0")) > 14:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_invoice(text: str) -> Invoice:
    values: dict = {}
    for field, names in LABELS.items():
        labels = "|".join(re.escape(name) for name in names)
        match = re.search(rf"^(?:{labels})\s*[:#]?\s+(.+)$", text, re.I | re.M)
        if match:
            raw = match.group(1).strip()
            if field in {"subtotal", "tax", "total"}:
                values[field] = money(raw)
            elif field.endswith("date"):
                values[field] = parse_date(raw)
            elif field == "currency":
                values[field] = raw.upper() if re.fullmatch(r"[A-Za-z]{3}", raw) else None
            else:
                values[field] = raw[: 300 if field in {"vendor", "customer"} else 100]
    items = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 5 and parts[0].lower() != "description":
            numbers = [money(part) for part in parts[1:]]
            if parts[0] and all(number is not None for number in numbers):
                items.append(
                    LineItem(
                        description=parts[0][:500],
                        quantity=numbers[0],
                        unit_price=numbers[1],
                        tax=numbers[2],
                        total=numbers[3],
                    )
                )
    values["line_items"] = items[:500]
    return Invoice.model_validate(values)


def validation_warnings(invoice: Invoice) -> list[str]:
    warnings = []
    for field in ["invoice_number", "invoice_date", "vendor", "customer", "currency", "total"]:
        if getattr(invoice, field) is None:
            warnings.append(
                f"Missing or unsupported {field.replace('_', ' ')}; review the document."
            )
    tolerance = Decimal("0.02")
    if all(getattr(invoice, field) is not None for field in ["subtotal", "tax", "total"]):
        if abs(invoice.subtotal + invoice.tax - invoice.total) > tolerance:
            warnings.append("Subtotal plus tax does not match total.")
    if invoice.invoice_date and invoice.due_date and invoice.due_date < invoice.invoice_date:
        warnings.append("Due date is earlier than invoice date.")
    for index, item in enumerate(invoice.line_items, start=1):
        missing = [
            field
            for field in ("quantity", "unit_price", "tax", "total")
            if getattr(item, field) is None
        ]
        if missing:
            warnings.append(f"Line {index}: missing {', '.join(missing)}; arithmetic needs review.")
            continue
        if item.quantity is not None and item.unit_price is not None and item.total is not None:
            expected = item.quantity * item.unit_price + (item.tax or Decimal(0))
            if abs(expected - item.total) > tolerance:
                warnings.append(
                    f"Line {index}: quantity times unit price plus tax does not match total."
                )
    totals = [item.total for item in invoice.line_items]
    if totals and all(value is not None for value in totals) and invoice.total is not None:
        if abs(sum(totals, Decimal(0)) - invoice.total) > tolerance:
            warnings.append("Line totals do not match invoice total.")
    if not invoice.line_items:
        warnings.append("No supported line-item table found; verify items manually.")
    return warnings
