"""Shared test helpers."""

import io
import warnings


def _make_minimal_pdf(text: str = "Hello World") -> bytes:
    """Build a tiny valid PDF with one page containing *text* using pypdf."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DictionaryObject,
        DecodedStreamObject,
        NameObject,
    )

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)

    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode())

    font_dict = DictionaryObject()
    font_dict[NameObject("/Type")] = NameObject("/Font")
    font_dict[NameObject("/Subtype")] = NameObject("/Type1")
    font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")

    font_res = DictionaryObject()
    font_res[NameObject("/F1")] = font_dict

    resources = DictionaryObject()
    resources[NameObject("/Font")] = font_res

    add_object = getattr(writer, "add_object", None)
    if add_object is None:
        warnings.warn(
            "PdfWriter.add_object() is unavailable; falling back to private "
            "PdfWriter._add_object() in test helper.",
            stacklevel=2,
        )
        add_object = writer._add_object

    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = add_object(stream)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
