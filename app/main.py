import asyncio
import csv
import io
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .middleware import BodyLimitMiddleware
from .models import ExtractionResult, Invoice
from .parser import validation_warnings
from .pdf import DocumentError, extract_text
from .providers import make_provider

logger = logging.getLogger("invoice_parser")
app = FastAPI(title="Invoice Document Parser", version="1.0.0")
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")
app.add_middleware(BodyLimitMiddleware)
processing_slots = asyncio.Semaphore(2)
request_times: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.path in {"/docs", "/docs/oauth2-redirect", "/redoc"}:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
    response.headers["Cache-Control"] = "no-store"
    return response


async def authorize(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    import secrets

    if settings.api_token:
        expected = f"Bearer {settings.api_token.get_secret_value()}"
        if not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), expected.encode()
        ):
            raise DocumentError("unauthorized", "A valid API access token is required.", 401)
    now = time.monotonic()
    client = request.client.host if request.client else "unknown"
    # Expire idle entries and cap state; this is a per-worker demo limiter.
    for key in list(request_times):
        if not request_times[key] or now - request_times[key][-1] >= 60:
            del request_times[key]
    if client not in request_times and len(request_times) >= 10000:
        raise DocumentError("rate_limit", "Server request capacity reached. Try again later.", 429)
    entries = request_times[client]
    while entries and now - entries[0] >= 60:
        entries.popleft()
    if len(entries) >= settings.requests_per_minute:
        raise DocumentError("rate_limit", "Too many requests. Try again in one minute.", 429)
    entries.append(now)


@app.exception_handler(DocumentError)
async def document_error(request: Request, exc: DocumentError):
    logger.warning(
        json.dumps({"event": "request_rejected", "code": exc.code, "status": exc.status})
    )
    headers = {"Retry-After": "60"} if exc.status == 429 else None
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message}},
        status_code=exc.status,
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        {"error": {"code": "validation_error", "message": "Request fields are invalid."}},
        status_code=422,
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.error(json.dumps({"event": "request_failed", "type": type(exc).__name__}))
    return JSONResponse(
        {"error": {"code": "internal_error", "message": "Request could not be processed."}},
        status_code=500,
    )


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(static / "index.html")


@app.get("/health")
async def health(settings: Annotated[Settings, Depends(get_settings)]):
    try:
        make_provider(settings)
    except ValueError:
        return JSONResponse({"status": "misconfigured"}, status_code=503)
    return {"status": "ok", "provider": settings.provider}


@app.post("/api/extract", response_model=ExtractionResult, dependencies=[Depends(authorize)])
async def extract(
    file: Annotated[UploadFile, File()], settings: Annotated[Settings, Depends(get_settings)]
):
    try:
        limit = settings.max_upload_mb * 1024 * 1024
        content = bytearray()
        while chunk := await file.read(65536):
            content.extend(chunk)
            if len(content) > limit:
                raise DocumentError(
                    "file_size", f"PDF must be at most {settings.max_upload_mb} MB.", 413
                )
        if not content:
            raise DocumentError("empty_file", "Choose a nonempty PDF file.")
        if processing_slots.locked():
            raise DocumentError("busy", "Document processors are busy. Try again shortly.", 503)
        await processing_slots.acquire()
        release_immediately = True
        try:
            task = asyncio.create_task(asyncio.to_thread(extract_text, bytes(content), settings))
            try:
                text, pages = await asyncio.wait_for(
                    asyncio.shield(task), settings.extraction_timeout_seconds
                )
            except asyncio.CancelledError:
                release_immediately = False
                task.add_done_callback(release_worker)
                raise
            except TimeoutError as exc:
                # Keep the slot occupied until the bounded worker actually exits.
                release_immediately = False
                task.add_done_callback(release_worker)
                raise DocumentError(
                    "timeout", "PDF processing timed out. Try a simpler document.", 408
                ) from exc
        finally:
            if release_immediately:
                processing_slots.release()
        try:
            provider = make_provider(settings)
        except ValueError as exc:
            raise DocumentError(
                "configuration", "Extraction provider is not configured.", 503
            ) from exc
        invoice = await provider.extract(text)
        warnings = validation_warnings(invoice)
        logger.info(
            json.dumps(
                {
                    "event": "extracted",
                    "pages": pages,
                    "provider": settings.provider,
                    "warning_count": len(warnings),
                }
            )
        )
        return ExtractionResult(
            invoice=invoice, warnings=warnings, provider=settings.provider, pages=pages
        )
    finally:
        await file.close()


def release_worker(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()
    processing_slots.release()


@app.post("/api/export/csv", dependencies=[Depends(authorize)])
async def export_csv(invoice: Invoice):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "invoice_number",
            "invoice_date",
            "due_date",
            "vendor",
            "customer",
            "currency",
            "subtotal",
            "invoice_tax",
            "invoice_total",
            "description",
            "quantity",
            "unit_price",
            "tax",
            "total",
        ]
    )
    rows = invoice.line_items or [None]
    for item in rows:
        values = [
            invoice.invoice_number,
            invoice.invoice_date,
            invoice.due_date,
            invoice.vendor,
            invoice.customer,
            invoice.currency,
            invoice.subtotal,
            invoice.tax,
            invoice.total,
            item.description if item else None,
            item.quantity if item else None,
            item.unit_price if item else None,
            item.tax if item else invoice.tax,
            item.total if item else invoice.total,
        ]
        # Neutralize spreadsheet formula injection in untrusted string fields.
        writer.writerow(
            [
                "'" + str(value)
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                else ""
                if value is None
                else value
                for value in values
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="invoice.csv"'},
    )
