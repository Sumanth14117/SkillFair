"""Bounded, in-memory PDF and DOCX resume text extraction."""

import os
import re
import struct
from io import BytesIO
from xml.parsers import expat
from zipfile import BadZipFile, ZipFile

from docx import Document
from fastapi import HTTPException
from pypdf import PdfReader


SUPPORTED_TYPES = {".pdf", ".docx"}

DEFAULT_MAX_PDF_PAGES = 100
HARD_MAX_PDF_PAGES = 500
DOCX_MAX_EXPANDED_BYTES = 50 * 1024 * 1024
DOCX_MAX_ENTRIES = 1000
DOCX_MAX_COMPRESSION_RATIO = 100
DOCX_MAX_XML_EXPANDED_BYTES = 20 * 1024 * 1024
DOCX_MAX_XML_ENTRY_BYTES = 10 * 1024 * 1024
DOCX_MAX_XML_ELEMENTS = 200_000
DOCX_MAX_XML_DEPTH = 64
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_CENTRAL_SIGNATURE = b"PK\x01\x02"
_ZIP_CENTRAL_HEADER_SIZE = 46
_ZIP_EOCD_SIZE = 22
_ZIP_MAX_COMMENT_SIZE = 65_535


def _preflight_docx_entry_count(content: bytes) -> None:
    """Count central-directory records before ZipFile allocates ZipInfo objects."""
    search_start = max(0, len(content) - _ZIP_EOCD_SIZE - _ZIP_MAX_COMMENT_SIZE)
    eocd_offset = content.rfind(_ZIP_EOCD_SIGNATURE, search_start)
    while eocd_offset >= 0:
        if eocd_offset + _ZIP_EOCD_SIZE <= len(content):
            comment_size = struct.unpack_from("<H", content, eocd_offset + 20)[0]
            if eocd_offset + _ZIP_EOCD_SIZE + comment_size == len(content):
                break
        eocd_offset = content.rfind(_ZIP_EOCD_SIGNATURE, search_start, eocd_offset)
    if eocd_offset < 0:
        raise ValueError("DOCX end record is missing or malformed")

    (_signature, disk_number, directory_disk, entries_on_disk, declared_entries,
     directory_size, _directory_offset, _comment_size) = struct.unpack_from(
        "<4s4H2LH", content, eocd_offset,
    )
    if disk_number or directory_disk or entries_on_disk != declared_entries:
        raise ValueError("Multi-disk DOCX archives are unsupported")
    if declared_entries == 0xFFFF:
        # ZIP64's sentinel necessarily exceeds this application's 1,000-entry cap.
        raise HTTPException(status_code=413, detail="The DOCX contains too many archive entries to process safely.")
    if declared_entries > DOCX_MAX_ENTRIES:
        raise HTTPException(status_code=413, detail="The DOCX contains too many archive entries to process safely.")
    if directory_size == 0xFFFFFFFF or _directory_offset == 0xFFFFFFFF:
        raise ValueError("DOCX central-directory offsets are unsupported")

    directory_start = eocd_offset - directory_size
    if directory_start < 0:
        raise ValueError("DOCX central-directory bounds are invalid")

    cursor = directory_start
    actual_entries = 0
    while cursor < eocd_offset:
        # The optional central-directory digital signature is not a file entry.
        if content[cursor:cursor + 4] == b"PK\x05\x05":
            if cursor + 6 > eocd_offset:
                raise ValueError("DOCX central-directory signature is truncated")
            signature_size = struct.unpack_from("<H", content, cursor + 4)[0]
            cursor += 6 + signature_size
            if cursor != eocd_offset:
                raise ValueError("DOCX central-directory signature bounds are invalid")
            break

        if cursor + _ZIP_CENTRAL_HEADER_SIZE > eocd_offset or content[cursor:cursor + 4] != _ZIP_CENTRAL_SIGNATURE:
            raise ValueError("DOCX central-directory record is malformed")
        filename_size, extra_size, comment_size = struct.unpack_from("<HHH", content, cursor + 28)
        record_size = _ZIP_CENTRAL_HEADER_SIZE + filename_size + extra_size + comment_size
        if cursor + record_size > eocd_offset:
            raise ValueError("DOCX central-directory record is truncated")
        actual_entries += 1
        if actual_entries > DOCX_MAX_ENTRIES:
            raise HTTPException(status_code=413, detail="The DOCX contains too many archive entries to process safely.")
        cursor += record_size

    if cursor != eocd_offset or actual_entries != declared_entries:
        raise ValueError("DOCX central-directory entry count is inconsistent")


def _pdf_page_limit() -> int:
    try:
        page_limit = int(os.getenv("SKILLFAIR_MAX_PDF_PAGES", str(DEFAULT_MAX_PDF_PAGES)))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="The PDF page limit is misconfigured.") from exc
    if not 1 <= page_limit <= HARD_MAX_PDF_PAGES:
        raise HTTPException(status_code=500, detail="The PDF page limit is misconfigured.")
    return page_limit


def _validate_xml_complexity(payload: bytes) -> None:
    # OOXML does not need DTDs or custom entities. Reject them before parsing to
    # avoid entity expansion and external-entity behavior.
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.IGNORECASE):
        raise ValueError("DOCX XML declarations are unsupported")

    parser = expat.ParserCreate()
    depth = 0
    element_count = 0

    def on_start(_name: str, _attributes: dict[str, str]) -> None:
        nonlocal depth, element_count
        depth += 1
        element_count += 1
        if depth > DOCX_MAX_XML_DEPTH or element_count > DOCX_MAX_XML_ELEMENTS:
            raise HTTPException(status_code=413, detail="The DOCX XML structure exceeds the safe processing limit.")

    def on_end(_name: str) -> None:
        nonlocal depth
        depth -= 1

    def reject_declaration(*_args: object) -> None:
        raise ValueError("DOCX XML declarations are unsupported")

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.Parse(payload, True)


def _inspect_docx_archive(content: bytes) -> None:
    _preflight_docx_entry_count(content)
    with ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > DOCX_MAX_ENTRIES:
            raise HTTPException(status_code=413, detail="The DOCX contains too many archive entries to process safely.")

        names = [entry.filename for entry in entries]
        if len(set(names)) != len(names):
            raise ValueError("DOCX archive contains duplicate entries")
        if "word/document.xml" not in names:
            raise ValueError("DOCX document part is missing")

        expanded_total = 0
        xml_total = 0
        for entry in entries:
            if entry.is_dir():
                continue
            if entry.file_size < 0 or entry.compress_size < 0:
                raise ValueError("DOCX archive metadata is invalid")
            expanded_total += entry.file_size
            if expanded_total > DOCX_MAX_EXPANDED_BYTES:
                raise HTTPException(status_code=413, detail="The DOCX expands beyond the safe processing limit.")

            if entry.file_size and (
                entry.compress_size == 0
                or entry.file_size / entry.compress_size > DOCX_MAX_COMPRESSION_RATIO
            ):
                raise HTTPException(status_code=400, detail="The DOCX archive uses suspicious compression and cannot be processed.")

            if entry.filename.lower().endswith((".xml", ".rels")):
                if entry.file_size > DOCX_MAX_XML_ENTRY_BYTES:
                    raise HTTPException(status_code=413, detail="A DOCX XML part exceeds the safe processing limit.")
                xml_total += entry.file_size
                if xml_total > DOCX_MAX_XML_EXPANDED_BYTES:
                    raise HTTPException(status_code=413, detail="The DOCX XML content exceeds the safe processing limit.")

        # Stream every member before python-docx parses the package. This checks
        # actual decompressed sizes against the central-directory metadata and
        # never extracts entries to disk.
        actual_expanded_total = 0
        for entry in entries:
            if entry.is_dir():
                if entry.file_size or entry.compress_size:
                    raise ValueError("DOCX directory metadata is invalid")
                continue
            is_xml = entry.filename.lower().endswith((".xml", ".rels"))
            actual_entry_size = 0
            xml_payload = bytearray() if is_xml else None
            with archive.open(entry, "r") as part:
                while chunk := part.read(64 * 1024):
                    actual_entry_size += len(chunk)
                    actual_expanded_total += len(chunk)
                    if actual_entry_size > entry.file_size:
                        raise ValueError("DOCX entry exceeds its declared size")
                    if actual_expanded_total > DOCX_MAX_EXPANDED_BYTES:
                        raise HTTPException(status_code=413, detail="The DOCX expands beyond the safe processing limit.")
                    if xml_payload is not None:
                        xml_payload.extend(chunk)
            if actual_entry_size != entry.file_size:
                raise ValueError("DOCX entry size does not match archive metadata")
            if xml_payload is not None:
                _validate_xml_complexity(bytes(xml_payload))


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
            page_limit = _pdf_page_limit()
            reader = PdfReader(BytesIO(content), strict=True)
            pages = reader.pages
            page_count = len(pages)
            if page_count > page_limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"The PDF exceeds the configured {page_limit}-page processing limit.",
                )
            # Do not extract text until the document page count has passed its limit.
            text = "\n".join(page.extract_text() or "" for page in pages)
        else:
            if not content.startswith(b"PK"):
                raise ValueError("invalid DOCX signature")
            _inspect_docx_archive(content)
            document = Document(BytesIO(content))
            text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
            for table in document.tables:
                for row in table.rows:
                    text += "\n" + " | ".join(cell.text for cell in row.cells)
    except HTTPException:
        raise
    except (ValueError, BadZipFile, OSError, KeyError, expat.ExpatError) as exc:
        raise HTTPException(status_code=400, detail="The file is not a valid, readable PDF or DOCX document.") from exc
    except Exception as exc:
        # Parser exceptions vary by document; never return parser details or contents.
        raise HTTPException(status_code=400, detail="The file could not be read. Try exporting the resume as PDF or DOCX.") from exc

    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="No selectable text was found. Scanned image-only resumes are not supported yet.")
    return text[:50_000]
