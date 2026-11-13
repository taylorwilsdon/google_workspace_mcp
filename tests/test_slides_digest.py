from gslides.slides_digest import format_deck, format_page, slide_title


def box(oid, text, size=None, placeholder=None):
    style = {"fontSize": {"magnitude": size, "unit": "PT"}} if size else {}
    shape = {
        "shapeType": "TEXT_BOX",
        "text": {
            "textElements": [
                {"paragraphMarker": {}},
                {"textRun": {"content": text + "\n", "style": style}},
            ]
        },
    }
    if placeholder:
        shape["placeholder"] = {"type": placeholder}
    return {"objectId": oid, "shape": shape}


def test_title_prefers_placeholder_then_largest_font():
    label, headline = box("a", "ANNOUNCEMENT", 8), box("b", "Quarterly Review", 17)
    assert slide_title({"pageElements": [label, headline]}) == "Quarterly Review"
    title = box("c", "Real title", 12, placeholder="TITLE")
    assert slide_title({"pageElements": [label, headline, title]}) == "Real title"


def test_title_prefers_inherited_size_body_over_sized_text():
    footnote = box("a", "Source: internal", 8)
    body = box("b", "Revenue grew 12%", placeholder="BODY")
    assert slide_title({"pageElements": [footnote, body]}) == "Revenue grew 12%"
    number = box("c", "7", placeholder="SLIDE_NUMBER")
    assert slide_title({"pageElements": [number, footnote]}) == "Source: internal"
    sized = box("d", "Fine print", 9, placeholder="BODY")
    assert (
        slide_title({"pageElements": [sized, box("e", "Headline", 30)]}) == "Headline"
    )


def test_page_lists_image_source_but_not_content_url():
    image = {
        "objectId": "i1",
        "image": {
            "sourceUrl": "https://example.com/a.png",
            "contentUrl": "https://lh7/long",
        },
    }
    out = format_page({"objectId": "p1", "pageElements": [image]}, revision="r1")
    assert "- image i1  src=https://example.com/a.png" in out
    assert "lh7" not in out


def test_deck_pages_by_slide_with_offset_note():
    slides = [
        {
            "objectId": f"s{i}",
            "slideProperties": {"layoutObjectId": "L"},
            "pageElements": [box(f"t{i}", f"Slide {i}", 20)],
        }
        for i in range(250)
    ]
    pres = {
        "presentationId": "p",
        "title": "Big",
        "slides": slides,
        "layouts": [{"objectId": "L"}],
    }
    first = format_deck(pres)
    assert "100. s99  Slide 99" in first and "101." not in first
    assert first.endswith(
        "[Showing slides 1 to 100 of 250; 150 more follow. Call again with offset=100 to read on.]"
    )
    last = format_deck(pres, offset=200)
    assert "201. s200  Slide 200" in last
    assert last.endswith("[Showing slides 201 to 250 of 250.]")
    assert "past the last slide" in format_deck(pres, offset=250)
    assert "Showing slides" not in format_deck({**pres, "slides": slides[:5]})
