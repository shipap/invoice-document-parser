from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    tax: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    total: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    due_date: date | None = None
    vendor: str | None = Field(default=None, max_length=300)
    customer: str | None = Field(default=None, max_length=300)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    subtotal: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    tax: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    total: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    line_items: list[LineItem] = Field(default_factory=list, max_length=500)


class ExtractionResult(BaseModel):
    invoice: Invoice
    warnings: list[str]
    provider: str
    pages: int
