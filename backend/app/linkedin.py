"""Parser for LinkedIn's own data export.

Users get this file from LinkedIn: Settings > Data privacy > Get a copy of your
data > Connections. LinkedIn emails a link to a .zip; the connections live in
`Connections.csv`. We never scrape or call LinkedIn. We only read the file the
user downloaded about their own network.

The CSV has had a few shapes over the years, so the parser is forgiving:
- a "Notes:" preamble of a few lines before the real header,
- header names that vary in case and wording ("URL" vs "Profile URL"),
- dates as "16 Oct 2026", "10/16/2026" or "2026-10-16",
- blank emails for anyone who hid theirs (most people).
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

from .util import clean_name, name_key

HEADER_ALIASES = {
    "first name": "first_name",
    "firstname": "first_name",
    "last name": "last_name",
    "lastname": "last_name",
    "url": "url",
    "profile url": "url",
    "linkedin url": "url",
    "email address": "email",
    "email": "email",
    "e-mail address": "email",
    "company": "company",
    "position": "position",
    "title": "position",
    "connected on": "connected_on",
    "connected": "connected_on",
}

DATE_FORMATS = ["%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y"]

MAX_ROWS = 30000


class ExportError(ValueError):
    """The upload is not a LinkedIn connections export we can read."""


@dataclass
class ConnectionRow:
    row: int
    first_name: str
    last_name: str
    name: str
    name_key: str
    url: str
    email: str
    company: str
    position: str
    connected_on: str | None  # ISO date


@dataclass
class ParsedExport:
    filename: str
    rows: list[ConnectionRow] = field(default_factory=list)
    skipped_blank: int = 0
    duplicates_in_file: int = 0


def parse_upload(filename: str, data: bytes) -> ParsedExport:
    """Accept either the export .zip or the Connections.csv inside it."""
    if not data:
        raise ExportError("The file is empty.")
    if data[:2] == b"PK" or filename.lower().endswith(".zip"):
        return _parse_zip(filename, data)
    return parse_csv(filename, data)


def _parse_zip(filename: str, data: bytes) -> ParsedExport:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExportError("That .zip could not be opened.") from exc
    member = None
    for info in archive.infolist():
        base = info.filename.rsplit("/", 1)[-1].lower()
        if base == "connections.csv":
            member = info
            break
    if member is None:
        raise ExportError(
            "No Connections.csv in this archive. When requesting your LinkedIn data, "
            "make sure Connections is selected."
        )
    if member.file_size > 50 * 1024 * 1024:
        raise ExportError("Connections.csv is unexpectedly large.")
    return parse_csv(f"{filename}:{member.filename}", archive.read(member))


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            if encoding == "utf-16" and "first" not in text.lower():
                continue
            return text
        except UnicodeDecodeError:
            continue
    raise ExportError("Could not read the file's text encoding.")


def _find_header(lines: list[str]) -> int:
    for i, line in enumerate(lines[:40]):
        low = line.lower()
        if "first name" in low and "last name" in low:
            return i
    raise ExportError(
        "This doesn't look like LinkedIn's Connections.csv (no 'First Name' / 'Last Name' header)."
    )


def parse_date(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalise_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    url = url.split("?", 1)[0].rstrip("/")
    url = url.replace("http://", "https://")
    if url.startswith("https://linkedin.com"):
        url = url.replace("https://linkedin.com", "https://www.linkedin.com", 1)
    return url.lower()


def parse_csv(filename: str, data: bytes) -> ParsedExport:
    text = _decode(data)
    lines = text.splitlines()
    start = _find_header(lines)
    reader = csv.reader(io.StringIO("\n".join(lines[start:])))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ExportError("The file has a header but no rows.") from exc
    columns = [HEADER_ALIASES.get(h.strip().lower()) for h in header]
    if "first_name" not in columns or "last_name" not in columns:
        raise ExportError("Missing First Name / Last Name columns.")

    parsed = ParsedExport(filename=filename)
    seen: set[str] = set()
    for index, raw in enumerate(reader, start=1):
        if index > MAX_ROWS:
            break
        values: dict[str, str] = {}
        for col, cell in zip(columns, raw):
            if col:
                values[col] = (cell or "").strip()
        first = values.get("first_name", "")
        last = values.get("last_name", "")
        name = clean_name(f"{first} {last}")
        if not name:
            parsed.skipped_blank += 1
            continue
        url = normalise_url(values.get("url", ""))
        dedupe = url or name_key(name)
        if dedupe in seen:
            parsed.duplicates_in_file += 1
            continue
        seen.add(dedupe)
        parsed.rows.append(
            ConnectionRow(
                row=index,
                first_name=clean_name(first),
                last_name=clean_name(last),
                name=name,
                name_key=name_key(name),
                url=url,
                email=values.get("email", ""),
                company=values.get("company", ""),
                position=values.get("position", ""),
                connected_on=parse_date(values.get("connected_on", "")),
            )
        )
    if not parsed.rows:
        raise ExportError("No connections found in the file.")
    return parsed
