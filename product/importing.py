"""Resume text extraction only; uploaded bytes are not saved."""

from io import BytesIO

from fastapi import HTTPException, Request
from pypdf import PdfReader


class ResumeError(ValueError):
    """Only user-facing validation messages may cross the API boundary."""


def extract(raw: bytes, media_type: str) -> str:
    if not raw:
        raise ValueError("Choose a resume file that contains text.")
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Use a resume smaller than 2 MiB, or paste its text.")
    if media_type == "application/pdf":
        try:
            pdf = PdfReader(BytesIO(raw))
            if pdf.is_encrypted or len(pdf.pages) > 20:
                raise ResumeError(
                    "Use an unlocked PDF with at most 20 pages, or paste its text."
                )
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except ResumeError:
            raise
        except Exception:
            raise ValueError(
                "This PDF could not be read. Paste the resume text instead."
            ) from None
    elif media_type in {"text/plain", "text/markdown", "application/octet-stream"}:
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ValueError(
                "Choose a UTF-8 text file or PDF, or paste the text."
            ) from None
    else:
        raise ValueError("Choose a PDF, TXT or Markdown file.")
    text = text.replace("\x00", "").strip()
    if len(text) < 20:
        raise ValueError(
            "No usable text was found. A scanned image needs text recognition first; you can paste the text here."
        )
    if len(text) > 40000:
        raise ValueError(
            "The text is too long. Paste the relevant resume text, up to 40,000 characters."
        )
    return text


def install_imports(app, _store):
    @app.post("/api/resume-text")
    async def resume_text(request: Request):
        from starlette.concurrency import run_in_threadpool

        raw = await request.body()
        try:
            return {
                "text": await run_in_threadpool(
                    extract, raw, request.headers.get("content-type", "").split(";")[0]
                )
            }
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
