from io import BytesIO
from zipfile import BadZipFile, ZipFile

from docx import Document
from fastapi import HTTPException
from pypdf import PdfReader


SUPPORTED_TYPES = {".pdf", ".docx"}


def validate_and_extract(filename: str | None, content: bytes, max_bytes: int) -> str:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    if suffix not in SUPPORTED_TYPES:
        raise HTTPException(status_code=415, detail="Upload a PDF or DOCX resume.")
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"The file exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")

    try:
        if suffix == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise ValueError("invalid PDF signature")
            reader = PdfReader(BytesIO(content), strict=True)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            if not content.startswith(b"PK"):
                raise ValueError("invalid DOCX signature")
            with ZipFile(BytesIO(content)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("missing Word document")
            document = Document(BytesIO(content))
            text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
            for table in document.tables:
                for row in table.rows:
                    text += "\n" + " | ".join(cell.text for cell in row.cells)
    except (ValueError, BadZipFile, OSError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="The file is not a valid, readable PDF or DOCX document.") from exc
    except Exception as exc:
        # Parser exceptions vary by document; do not expose parser details.
        raise HTTPException(status_code=400, detail="The file could not be read. Try exporting the resume as PDF or DOCX.") from exc

    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="No selectable text was found. Scanned image-only resumes are not supported yet.")
    return text[:50_000]
