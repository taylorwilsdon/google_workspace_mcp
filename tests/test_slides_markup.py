from gslides.slides_markup import project_box, project_page

EMU = 12700


def size(w_pt, h_pt):
    return {
        "width": {"magnitude": w_pt * EMU, "unit": "EMU"},
        "height": {"magnitude": h_pt * EMU, "unit": "EMU"},
    }


def test_box_unrotated_scaled():
    m = (2.0, 0.0, 0.0, 0.5, 10 * EMU, 20 * EMU)
    assert project_box(m, size(100, 40)) == {"x": 10, "y": 20, "w": 200, "h": 20}


def test_box_rotated_90_keeps_centre():
    # 90 degrees clockwise about the box centre (50, 20) for a 100 x 40 box at the origin.
    m = (0.0, 1.0, -1.0, 0.0, 70 * EMU, -30 * EMU)
    assert project_box(m, size(100, 40)) == {
        "x": 0,
        "y": 0,
        "w": 100,
        "h": 40,
        "rot": 90,
    }


def test_box_flip_and_skew():
    assert project_box((1.0, 0.0, 0.0, -1.0, 0, 40 * EMU), size(100, 40))["flip"] == "v"
    assert project_box((1.0, 0.0, 0.5, 1.0, 0, 0), size(100, 40))["skewed"] is True


def run(text, **style):
    return {"textRun": {"content": text, "style": style}}


def para(**bullet):
    marker = {"style": {}}
    if bullet:
        marker["bullet"] = bullet
    return {"paragraphMarker": marker}


def shape_page(elements, **shape_extra):
    return {
        "objectId": "p1",
        "pageElements": [
            {
                "objectId": "e1",
                "size": size(100, 40),
                "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
                "shape": {
                    "shapeType": "TEXT_BOX",
                    "text": {"textElements": elements},
                    **shape_extra,
                },
            }
        ],
    }


def test_text_bare_strings_styles_and_merge():
    out = project_page(
        shape_page(
            [
                para(),
                run("Checkout", bold=True),
                run(" conversion"),
                run(" is up\n"),
                para(),
                run("Plain\n"),
            ]
        )
    )
    el = out["elements"][0]
    assert out["styles"] == {"s1": {"bold": True}}
    assert el["text"] == [
        {"runs": [{"text": "Checkout", "s": "s1"}, " conversion is up"]},
        "Plain",
    ]
    assert len(el["v"]) == 8


def test_bullets_and_glyphs():
    out = project_page(
        shape_page(
            [
                para(listId="kix.a", nestingLevel=0, glyph="●"),
                run("One\n"),
                para(listId="kix.a", nestingLevel=1, glyph="○"),
                run("Two\n"),
            ]
        )
    )
    el = out["elements"][0]
    # One list in the element: the bullet is just its level.
    assert el["text"] == [{"text": "One", "bullet": 0}, {"text": "Two", "bullet": 1}]
    assert el["lists"] == {"L1": {"glyphs": {"0": "●", "1": "○"}}}


def test_v_ignores_alias_names():
    a = project_page(shape_page([para(), run("x", italic=True), run("y\n", bold=True)]))
    b = shape_page([para(), run("x", italic=True), run("y\n", bold=True)])
    first = shape_page([para(), run("z\n", bold=True)])["pageElements"][0]
    b["pageElements"].insert(0, {**first, "objectId": "e0"})
    b = project_page(b)
    assert a["styles"]["s1"] == {"italic": True}
    assert b["styles"]["s1"] == {"bold": True}
    assert a["elements"][0]["v"] == b["elements"][1]["v"]


def test_v_of_a_group_child_matches_whole_page_and_targeted_reads():
    page = shape_page([para(), run("x\n")])
    child = page["pageElements"][0]
    page["pageElements"] = [{"objectId": "g1", "elementGroup": {"children": [child]}}]
    whole = project_page(page)["elements"][0]["children"][0]
    targeted = project_page(page, targets=["e1"])["elements"][0]
    assert targeted["group"] == "g1"
    assert whole["v"] == targeted["v"]


def test_shrunk_sizes_are_displayed_for_text_and_auto_text():
    size = {"fontSize": {"magnitude": 20, "unit": "PT"}}
    auto = {"autoText": {"type": "SLIDE_NUMBER", "content": "3", "style": size}}
    shrunk = {"autofit": {"autofitType": "TEXT_AUTOFIT", "fontScale": 0.5}}
    out = project_page(
        shape_page([para(), run("Page ", **size), auto], shapeProperties=shrunk)
    )
    assert out["styles"] == {"s1": {"size": 10}}
    assert out["elements"][0]["text"] == [
        {"runs": [{"text": "Page ", "s": "s1"}, {"auto": "SLIDE_NUMBER", "s": "s1"}]}
    ]


def test_table_merge_and_scoped_group_child():
    def cell(r, c, t, **span):
        return {
            "location": {"rowIndex": r, "columnIndex": c},
            "text": {"textElements": [para(), run(t + "\n")]},
            **span,
        }

    page = {
        "objectId": "p1",
        "pageElements": [
            {
                "objectId": "g1",
                "transform": {
                    "scaleX": 1,
                    "scaleY": 1,
                    "translateX": 10 * EMU,
                    "unit": "EMU",
                },
                "elementGroup": {
                    "children": [
                        {
                            "objectId": "t1",
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateY": 5 * EMU,
                                "unit": "EMU",
                            },
                            "table": {
                                "rows": 1,
                                "columns": 2,
                                "tableColumns": [
                                    {
                                        "columnWidth": {
                                            "magnitude": 50 * EMU,
                                            "unit": "EMU",
                                        }
                                    }
                                ]
                                * 2,
                                "tableRows": [
                                    {
                                        "rowHeight": {
                                            "magnitude": 20 * EMU,
                                            "unit": "EMU",
                                        },
                                        "tableCells": [
                                            cell(0, 0, "A", columnSpan=2),
                                            cell(0, 1, ""),
                                        ],
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        ],
    }
    out = project_page(page, targets=["t1"])
    t = out["elements"][0]
    assert t["group"] == "g1"
    assert t["box"] == {"x": 10, "y": 5}
    assert t["rows"] == [{"r": 0, "cells": [{"text": ["A"], "span": [1, 2]}, None]}]


def test_editor_defaults_are_omitted():
    defaults = {
        "contentAlignment": "TOP",
        "shapeBackgroundFill": {"propertyState": "NOT_RENDERED"},
        "outline": {"propertyState": "NOT_RENDERED"},
    }
    el = project_page(shape_page([para(), run("x\n")], shapeProperties=defaults))[
        "elements"
    ][0]
    # No kind and no shape: a text box.
    assert "kind" not in el and "shape" not in el
    assert not {"content_align", "fill", "outline"} & set(el)

    page = shape_page(
        [para(), run("x\n")], shapeProperties={**defaults, "contentAlignment": "MIDDLE"}
    )
    page["pageElements"][0]["shape"]["shapeType"] = "RECTANGLE"
    el = project_page(page)["elements"][0]
    # MIDDLE is a rectangle's default; "none" on a rectangle is a real choice.
    assert "content_align" not in el
    assert el["fill"] == "none" and el["outline"] == "none"


def test_box_is_whole_points():
    m = (1.0, 0.0, 0.0, 1.0, 10.4 * EMU, 20.6 * EMU)
    assert project_box(m, size(100.3, 40)) == {"x": 10, "y": 21, "w": 100, "h": 40}


def test_two_lists_keep_list_names():
    out = project_page(
        shape_page(
            [
                para(listId="a", nestingLevel=0),
                run("One\n"),
                para(listId="b", nestingLevel=0),
                run("Two\n"),
            ]
        )
    )
    assert [p["bullet"] for p in out["elements"][0]["text"]] == [
        {"list": "L1", "level": 0},
        {"list": "L2", "level": 0},
    ]


def test_implied_styles_and_paragraph_defaults_are_omitted():
    link = {"url": "https://example.com"}
    hyper = {"opaqueColor": {"themeColor": "HYPERLINK"}}
    elements = [
        {"paragraphMarker": {"style": {"alignment": "START", "lineSpacing": 100}}},
        run(
            "Bold", bold=True, weightedFontFamily={"fontFamily": "Arial", "weight": 700}
        ),
        run(" link\n", link=link, underline=True, foregroundColor=hyper),
        {"paragraphMarker": {"style": {"alignment": "CENTER", "lineSpacing": 115}}},
        run("Centred\n"),
    ]
    out = project_page(shape_page(elements))
    assert out["styles"] == {
        "s1": {"bold": True, "font": "Arial"},
        "s2": {"link": "https://example.com"},
    }
    assert out["elements"][0]["text"] == [
        {"runs": [{"text": "Bold", "s": "s1"}, {"text": " link", "s": "s2"}]},
        {"text": "Centred", "align": "CENTER", "line_spacing": 115},
    ]


def test_transparent_fill_reads_as_none():
    clear = {
        "shapeBackgroundFill": {"solidFill": {"alpha": 0, "color": {"rgbColor": {}}}}
    }
    el = project_page(shape_page([para(), run("x\n")], shapeProperties=clear))[
        "elements"
    ][0]
    assert "fill" not in el  # "none" on a text box is its default
    half = {
        "shapeBackgroundFill": {
            "solidFill": {"alpha": 0.5, "color": {"rgbColor": {"red": 1}}}
        }
    }
    el = project_page(shape_page([para(), run("x\n")], shapeProperties=half))[
        "elements"
    ][0]
    assert el["fill"] == {"color": "#ff0000", "alpha": 0.5}


def test_omitted_scale_is_zero_for_a_quarter_turn():
    page = shape_page([para(), run("x\n")])
    page["pageElements"][0]["transform"] = {
        "shearX": -1,
        "shearY": 1,
        "translateX": 70 * EMU,
        "translateY": -30 * EMU,
        "unit": "EMU",
    }
    box = project_page(page)["elements"][0]["box"]
    assert box == {"x": 0, "y": 0, "w": 100, "h": 40, "rot": 90}


def test_placeholder_text_keeps_false_and_zero_overrides():
    zero = {"unit": "PT"}
    elements = [
        {"paragraphMarker": {"style": {"spaceAbove": zero}}},
        run("Plain\n", bold=False),
    ]
    placeholder = shape_page(elements, placeholder={"type": "BODY"})
    out = project_page(placeholder)
    assert out["styles"] == {"s1": {"bold": False}}
    assert out["elements"][0]["text"] == [
        {"text": "Plain", "s": "s1", "space_above": 0}
    ]
    out = project_page(shape_page(elements))
    assert "styles" not in out
    assert out["elements"][0]["text"] == ["Plain"]


def test_shrink_reports_line_spacing_reduction():
    autofit = {
        "autofitType": "TEXT_AUTOFIT",
        "fontScale": 0.8,
        "lineSpacingReduction": 0.2,
    }
    el = project_page(
        shape_page([para(), run("x\n")], shapeProperties={"autofit": autofit})
    )["elements"][0]
    assert el["autofit"] == {
        "type": "shrink",
        "scale": 0.8,
        "line_spacing_reduction": 0.2,
    }


def test_merged_cells_keep_later_cells_in_their_columns():
    def cell(c, t, **span):
        return {
            "location": {"columnIndex": c},
            "text": {"textElements": [para(), run(t + "\n")]},
            **span,
        }

    table = {
        "objectId": "t1",
        "table": {
            "rows": 1,
            "columns": 3,
            "tableRows": [{"tableCells": [cell(0, "A", columnSpan=2), cell(2, "C")]}],
        },
    }
    out = project_page({"objectId": "p1", "pageElements": [table]})
    assert out["elements"][0]["rows"] == [
        {"r": 0, "cells": [{"text": ["A"], "span": [1, 2]}, None, "C"]}
    ]


def test_v_changes_with_the_bullet_glyph():
    def body(glyph):
        return project_page(
            shape_page([para(listId="a", nestingLevel=0, glyph=glyph), run("One\n")])
        )["elements"][0]["v"]

    assert body("●") != body("○")


def test_selected_group_does_not_repeat_a_selected_child():
    page = shape_page([para(), run("x\n")])
    child = page["pageElements"][0]
    page["pageElements"] = [{"objectId": "g1", "elementGroup": {"children": [child]}}]
    out = project_page(page, targets=["g1", "e1"])
    assert [e["id"] for e in out["elements"]] == ["g1"]


def test_v_changes_when_two_lists_swap_glyphs():
    def body(first, second):
        return project_page(
            shape_page(
                [
                    para(listId="a", nestingLevel=0, glyph=first),
                    run("One\n"),
                    para(listId="b", nestingLevel=0, glyph=second),
                    run("Two\n"),
                ]
            )
        )["elements"][0]["v"]

    assert body("●", "○") != body("○", "●")
