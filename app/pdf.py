from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .config import Settings


class DocumentError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def extract_text(content: bytes, settings: Settings) -> tuple[str, int]:
    if not content.startswith(b"%PDF-"):
        raise DocumentError("invalid_pdf", "Upload a valid PDF document.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise DocumentError("encrypted_pdf", "Password-protected PDFs are not supported.")
        pages = len(reader.pages)
        if not pages:
            raise DocumentError("empty_pdf", "The PDF contains no pages.")
        if pages > settings.max_pdf_pages:
            raise DocumentError(
                "page_limit", f"PDF exceeds the {settings.max_pdf_pages}-page limit."
            )
        parts = []
        chars = 0
        blank_pages = 0
        for page in reader.pages:
            content_stream = page.get_contents()
            if content_stream and len(content_stream.get_data()) > 5_000_000:
                raise DocumentError("complex_pdf", "PDF page content is too complex to process.")
            part = page.extract_text() or ""
            blank_pages += not bool(part.strip())
            chars += len(part)
            if chars > settings.max_text_chars:
                raise DocumentError("text_limit", "Extracted text exceeds the configured limit.")
            parts.append(part)
        text = "\n".join(parts)
        if len(text.strip()) < 20 or blank_pages:
            raise DocumentError(
                "ocr_required",
                "This PDF has pages without extractable text. "
                "It may be scanned. OCR is required and is not enabled.",
            )
        return text, pages
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as exc:
        raise DocumentError(
            "invalid_pdf", "PDF could not be read. Export a new text-based PDF."
        ) from exc
