import io
import zipfile

import pytest

from app.linkedin import ExportError, parse_csv, parse_date, parse_upload


def test_sample_export_parses(sample_csv):
    parsed = parse_csv("Connections.csv", sample_csv)
    names = [r.name for r in parsed.rows]
    assert "Priya Raman" in names
    assert parsed.duplicates_in_file == 1  # Arjun Mehta twice
    assert parsed.skipped_blank == 1
    priya = next(r for r in parsed.rows if r.name == "Priya Raman")
    assert priya.company == "Monsoon Climate Fund"
    assert priya.connected_on == "2026-10-12"
    assert priya.url == "https://www.linkedin.com/in/priya-raman-3a1b2c"


def test_names_are_cleaned(sample_csv):
    parsed = parse_csv("Connections.csv", sample_csv)
    names = {r.name for r in parsed.rows}
    assert "Riya Sen" in names  # emoji stripped
    assert "Manish Jain" in names  # credential stripped


def test_zip_is_unwrapped(sample_csv):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Basic_LinkedInDataExport_10-16-2026/Connections.csv", sample_csv)
        z.writestr("Basic_LinkedInDataExport_10-16-2026/Profile.csv", "x")
    parsed = parse_upload("export.zip", buf.getvalue())
    assert len(parsed.rows) > 30


def test_zip_without_connections_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Profile.csv", "x")
    with pytest.raises(ExportError):
        parse_upload("export.zip", buf.getvalue())


def test_other_csv_is_rejected():
    with pytest.raises(ExportError):
        parse_csv("x.csv", b"name,email\nA,b@c.d\n")


def test_header_variants_and_dates():
    data = (
        "first name,last name,Profile URL,E-mail Address,Company,Title,Connected\n"
        "Asha,Rao,http://linkedin.com/in/asha/?trk=x,,Acme,CTO,10/16/2026\n"
    ).encode("utf-8-sig")
    parsed = parse_csv("c.csv", data)
    row = parsed.rows[0]
    assert row.url == "https://www.linkedin.com/in/asha"
    assert row.position == "CTO"
    assert row.connected_on == "2026-10-16"


@pytest.mark.parametrize("raw,iso", [("16 Oct 2026", "2026-10-16"), ("2026-10-16", "2026-10-16"), ("", None), ("soon", None)])
def test_parse_date(raw, iso):
    assert parse_date(raw) == iso
