from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import Invoice, LineItem
from app.parser import money, parse_invoice, validation_warnings

SAMPLE = Path(__file__).parents[1] / "examples/sample-invoice.txt"


def test_fictional_invoice_has_exact_totals_and_items():
    invoice = parse_invoice(SAMPLE.read_text())
    assert invoice.invoice_number == "NG-2026-1042"
    assert invoice.invoice_date == date(2026, 9, 1)
    assert invoice.total == Decimal("550.00")
    assert len(invoice.line_items) == 2
    assert validation_warnings(invoice) == []


def test_ambiguous_amounts_and_impossible_dates_are_not_invented():
    invoice = parse_invoice("Total: 1.234,56\nInvoice date: 2026-02-30\nCurrency: euro")
    assert invoice.total is None
    assert invoice.invoice_date is None
    assert invoice.currency is None
    assert len(validation_warnings(invoice)) >= 6
    assert money("USD 1,234.56") == Decimal("1234.56")
    assert money("12,34") is None


def test_arithmetic_mismatch_and_due_date_are_flagged():
    invoice = Invoice(
        invoice_date=date(2026, 9, 1),
        due_date=date(2026, 8, 1),
        subtotal="100",
        tax="10",
        total="125",
        line_items=[
            LineItem(description="Service", quantity="2", unit_price="50", tax="10", total="120")
        ],
    )
    warnings = validation_warnings(invoice)
    assert "Subtotal plus tax does not match total." in warnings
    assert "Due date is earlier than invoice date." in warnings
    assert any("Line 1" in warning for warning in warnings)
    assert "Line totals do not match invoice total." in warnings


def test_oversized_demo_amount_stays_unknown():
    assert parse_invoice("Total: 12345678901234567890.00").total is None


def test_unknown_line_tax_is_not_assumed_to_be_zero():
    invoice = Invoice(
        line_items=[
            LineItem(description="Service", quantity="2", unit_price="50", tax=None, total="110")
        ]
    )
    warnings = validation_warnings(invoice)
    assert any("missing tax" in warning for warning in warnings)
    assert not any("quantity times" in warning for warning in warnings)
