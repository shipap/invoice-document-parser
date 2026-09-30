from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import Settings, get_settings
from app.main import app, request_times

SAMPLE = Path(__file__).parents[1] / "examples/sample-invoice.pdf"


@pytest.fixture
def client():
    app.dependency_overrides[get_settings] = lambda: Settings(
        provider="demo", api_token=None, _env_file=None
    )
    request_times.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_and_ui(client):
    assert client.get("/health").json()["status"] == "ok"
    response = client.get("/")
    assert response.status_code == 200
    assert "Invoice document parser" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_pdf_upload_and_csv_round_trip(client):
    response = client.post(
        "/api/extract", files={"file": ("../../evil.pdf", SAMPLE.read_bytes(), "application/pdf")}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["invoice"]["total"] == "550.00"
    assert result["warnings"] == []
    export = client.post("/api/export/csv", json=result["invoice"])
    assert export.status_code == 200
    assert "Design consultation,2,150.00,30.00,330.00" in export.text
    assert "550.00" not in export.headers["content-disposition"]


def test_non_pdf_and_missing_file_are_rejected(client):
    response = client.post(
        "/api/extract", files={"file": ("fake.pdf", b"not PDF", "application/pdf")}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pdf"
    assert client.post("/api/extract").json()["error"]["code"] == "validation_error"


def test_scan_and_encrypted_pdf_are_explained(client):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    data = BytesIO()
    writer.write(data)
    response = client.post("/api/extract", files={"file": ("scan.pdf", data.getvalue())})
    assert response.json()["error"]["code"] == "ocr_required"
    writer.encrypt("fictional-test-only")
    data = BytesIO()
    writer.write(data)
    response = client.post("/api/extract", files={"file": ("locked.pdf", data.getvalue())})
    assert response.json()["error"]["code"] == "encrypted_pdf"


def test_size_page_and_rate_limits(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        max_upload_mb=1, max_pdf_pages=1, requests_per_minute=2, _env_file=None
    )
    response = client.post("/api/extract", files={"file": ("large.pdf", b"x" * (1024 * 1024 + 1))})
    assert response.status_code == 413
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    data = BytesIO()
    writer.write(data)
    response = client.post("/api/extract", files={"file": ("pages.pdf", data.getvalue())})
    assert response.json()["error"]["code"] == "page_limit"
    response = client.post("/api/export/csv", json={})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_auth_and_formula_injection(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_token="test-token-only", _env_file=None
    )
    assert client.post("/api/export/csv", json={}).status_code == 401
    response = client.post(
        "/api/export/csv",
        json={"vendor": "=SUM(A1:A2)"},
        headers={"Authorization": "Bearer test-token-only"},
    )
    assert response.status_code == 200
    assert "'=SUM(A1:A2)" in response.text


def test_negative_financial_fields_rejected(client):
    assert client.post("/api/export/csv", json={"total": "-1"}).status_code == 422


def test_nonfinite_amounts_rejected(client):
    assert client.post("/api/export/csv", json={"total": "Infinity"}).status_code == 422
    assert client.post("/api/export/csv", json={"total": "NaN"}).status_code == 422


def test_body_limit_runs_before_multipart_parser(client, monkeypatch):
    monkeypatch.setattr(
        "app.middleware.get_settings", lambda: Settings(max_upload_mb=1, _env_file=None)
    )
    response = client.post(
        "/api/extract",
        content=b"x" * (1024 * 1024 + 65537),
        headers={"Content-Type": "multipart/form-data; boundary=broken"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_size"


def test_csv_preserves_invoice_level_fields(client):
    import csv
    import io

    result = client.post(
        "/api/extract", files={"file": ("invoice.pdf", SAMPLE.read_bytes())}
    ).json()
    response = client.post("/api/export/csv", json=result["invoice"])
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["due_date"] == result["invoice"]["due_date"]
    assert rows[0]["subtotal"] == result["invoice"]["subtotal"]
    assert all(row["invoice_tax"] == result["invoice"]["tax"] for row in rows)
    assert all(row["invoice_total"] == "550.00" for row in rows)


def test_api_docs_assets_are_allowed_without_relaxing_app_policy(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert "unsafe-inline" not in client.get("/").headers["content-security-policy"]
