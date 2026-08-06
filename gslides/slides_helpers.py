"""
Google Slides Helper Functions

Shared utilities for Google Slides operations.
"""

import asyncio
from copy import deepcopy
from typing import Any, Dict, List, Set, Tuple

from core.utils import UserInputError

_PRESENTATION_PAGE_ID_FIELDS = (
    "slides(objectId,slideProperties(notesPage(objectId))),"
    "masters(objectId),layouts(objectId),notesMaster(objectId)"
)

_SLIDES_BATCH_REQUEST_TYPES = frozenset(
    {
        "createSlide",
        "createShape",
        "createTable",
        "insertText",
        "insertTableRows",
        "insertTableColumns",
        "deleteTableRow",
        "deleteTableColumn",
        "replaceAllText",
        "deleteObject",
        "updatePageElementTransform",
        "updateSlidesPosition",
        "deleteText",
        "createImage",
        "createVideo",
        "createSheetsChart",
        "createLine",
        "refreshSheetsChart",
        "updateShapeProperties",
        "updateImageProperties",
        "updateVideoProperties",
        "updatePageProperties",
        "updateTableCellProperties",
        "updateLineProperties",
        "createParagraphBullets",
        "replaceAllShapesWithImage",
        "duplicateObject",
        "updateTextStyle",
        "replaceAllShapesWithSheetsChart",
        "deleteParagraphBullets",
        "updateParagraphStyle",
        "updateTableBorderProperties",
        "updateTableColumnProperties",
        "updateTableRowProperties",
        "mergeTableCells",
        "unmergeTableCells",
        "groupObjects",
        "ungroupObjects",
        "updatePageElementAltText",
        "replaceImage",
        "updateSlideProperties",
        "updatePageElementsZOrder",
        "updateLineCategory",
        "rerouteLine",
    }
)

_SLIDES_BATCH_REQUEST_EXAMPLES = (
    "createSlide",
    "createShape",
    "insertText",
    "updateTextStyle",
    "createImage",
    "deleteObject",
)


def _nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _strict_object(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array_items(items: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "array", "items": items}


def _string_array(description: str) -> Dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {"type": "string"},
    }


def _integer_array(description: str) -> Dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {"type": "integer"},
    }


def _nullable_string(description: str) -> Dict[str, Any]:
    return {
        "type": ["string", "null"],
        "description": description,
    }


def _nullable_number(description: str) -> Dict[str, Any]:
    return {
        "type": ["number", "null"],
        "description": description,
    }


def _nullable_integer(description: str) -> Dict[str, Any]:
    return {
        "type": ["integer", "null"],
        "description": description,
    }


def _nullable_boolean(description: str) -> Dict[str, Any]:
    return {
        "type": ["boolean", "null"],
        "description": description,
    }


_DIMENSION_SCHEMA = _strict_object(
    {
        "magnitude": _nullable_number("Dimension magnitude."),
        "unit": _nullable_string("Dimension unit such as EMU or PT."),
    }
)

_SIZE_SCHEMA = _strict_object(
    {
        "height": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "width": _nullable(deepcopy(_DIMENSION_SCHEMA)),
    }
)

_AFFINE_TRANSFORM_SCHEMA = _strict_object(
    {
        "scaleX": _nullable_number("Horizontal scale."),
        "scaleY": _nullable_number("Vertical scale."),
        "shearX": _nullable_number("Horizontal shear."),
        "shearY": _nullable_number("Vertical shear."),
        "translateX": _nullable_number("Horizontal translation."),
        "translateY": _nullable_number("Vertical translation."),
        "unit": _nullable_string("Transform unit such as EMU."),
    }
)

_ELEMENT_PROPERTIES_SCHEMA = _strict_object(
    {
        "pageObjectId": {
            "type": "string",
            "description": "Target slide/page object ID.",
        },
        "size": _nullable(deepcopy(_SIZE_SCHEMA)),
        "transform": _nullable(deepcopy(_AFFINE_TRANSFORM_SCHEMA)),
    }
)

_TEXT_RANGE_SCHEMA = _strict_object(
    {
        "type": {
            "type": "string",
            "description": "Text range type, usually ALL or FIXED_RANGE.",
        },
        "startIndex": _nullable_integer("Start index for FIXED_RANGE."),
        "endIndex": _nullable_integer("End index for FIXED_RANGE."),
    }
)

_RGB_COLOR_SCHEMA = _strict_object(
    {
        "red": _nullable_number("Red channel from 0 to 1."),
        "green": _nullable_number("Green channel from 0 to 1."),
        "blue": _nullable_number("Blue channel from 0 to 1."),
    }
)

_OPAQUE_COLOR_SCHEMA = _strict_object(
    {
        "rgbColor": _nullable(deepcopy(_RGB_COLOR_SCHEMA)),
        "themeColor": _nullable_string("Theme color name."),
    }
)

_OPTIONAL_COLOR_SCHEMA = _strict_object(
    {
        "opaqueColor": _nullable(deepcopy(_OPAQUE_COLOR_SCHEMA)),
    }
)

_SOLID_FILL_SCHEMA = _strict_object(
    {
        "color": _nullable(deepcopy(_OPAQUE_COLOR_SCHEMA)),
        "alpha": _nullable_number("Alpha value from 0 to 1."),
    }
)

_LINK_SCHEMA = _strict_object(
    {
        "url": _nullable_string("External URL."),
        "relativeLink": _nullable_string("Relative slide link type."),
        "pageObjectId": _nullable_string("Linked page object ID."),
        "slideIndex": _nullable_integer("Linked slide index."),
    }
)

_WEIGHTED_FONT_FAMILY_SCHEMA = _strict_object(
    {
        "fontFamily": _nullable_string("Font family name."),
        "weight": _nullable_integer("Font weight, for example 400 or 700."),
    }
)

_TEXT_STYLE_SCHEMA = _strict_object(
    {
        "backgroundColor": _nullable(deepcopy(_OPTIONAL_COLOR_SCHEMA)),
        "baselineOffset": _nullable_string("Text baseline offset."),
        "bold": _nullable_boolean("Whether text is bold."),
        "fontFamily": _nullable_string("Font family name."),
        "italic": _nullable_boolean("Whether text is italic."),
        "link": _nullable(deepcopy(_LINK_SCHEMA)),
        "smallCaps": _nullable_boolean("Whether text uses small caps."),
        "strikethrough": _nullable_boolean("Whether text is struck through."),
        "underline": _nullable_boolean("Whether text is underlined."),
        "fontSize": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "foregroundColor": _nullable(deepcopy(_OPTIONAL_COLOR_SCHEMA)),
        "weightedFontFamily": _nullable(deepcopy(_WEIGHTED_FONT_FAMILY_SCHEMA)),
    }
)

_LINE_FILL_SCHEMA = _strict_object(
    {
        "solidFill": _nullable(deepcopy(_SOLID_FILL_SCHEMA)),
    }
)

_OUTLINE_SCHEMA = _strict_object(
    {
        "outlineFill": _nullable(deepcopy(_LINE_FILL_SCHEMA)),
        "weight": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "dashStyle": _nullable_string("Outline dash style."),
        "propertyState": _nullable_string("Outline property state."),
    }
)

_SHADOW_SCHEMA = _strict_object(
    {
        "type": _nullable_string("Shadow type."),
        "transform": _nullable(deepcopy(_AFFINE_TRANSFORM_SCHEMA)),
        "alignment": _nullable_string("Shadow alignment."),
        "blurRadius": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "color": _nullable(deepcopy(_OPAQUE_COLOR_SCHEMA)),
        "alpha": _nullable_number("Shadow alpha value from 0 to 1."),
        "rotateWithShape": _nullable_boolean(
            "Whether the shadow rotates with the shape."
        ),
        "propertyState": _nullable_string("Shadow property state."),
    }
)

_AUTOFIT_SCHEMA = _strict_object(
    {
        "autofitType": _nullable_string("Autofit type."),
        "fontScale": _nullable_number("Font scale applied by autofit."),
        "lineSpacingReduction": _nullable_number(
            "Line spacing reduction applied by autofit."
        ),
    }
)

_LINE_CONNECTION_SCHEMA = _strict_object(
    {
        "connectedObjectId": _nullable_string("Connected page element object ID."),
        "connectionSiteIndex": _nullable_integer("Connection site index."),
    }
)

_CROP_PROPERTIES_SCHEMA = _strict_object(
    {
        "leftOffset": _nullable_number("Left crop offset."),
        "rightOffset": _nullable_number("Right crop offset."),
        "topOffset": _nullable_number("Top crop offset."),
        "bottomOffset": _nullable_number("Bottom crop offset."),
        "angle": _nullable_number("Crop rotation angle."),
    }
)

_IMAGE_PROPERTIES_SCHEMA = _strict_object(
    {
        "cropProperties": _nullable(deepcopy(_CROP_PROPERTIES_SCHEMA)),
        "transparency": _nullable_number("Image transparency value."),
        "brightness": _nullable_number("Image brightness value."),
        "contrast": _nullable_number("Image contrast value."),
        "outline": _nullable(deepcopy(_OUTLINE_SCHEMA)),
        "recolor": _nullable(
            _strict_object(
                {
                    "name": _nullable_string("Recolor effect name."),
                    "recolorStops": _nullable(
                        _array_items(
                            _strict_object(
                                {
                                    "alpha": _nullable_number(
                                        "Recolor stop alpha value."
                                    ),
                                    "color": _nullable(deepcopy(_OPAQUE_COLOR_SCHEMA)),
                                    "position": _nullable_number(
                                        "Recolor stop position."
                                    ),
                                }
                            )
                        )
                    ),
                }
            )
        ),
        "shadow": _nullable(deepcopy(_SHADOW_SCHEMA)),
        "link": _nullable(deepcopy(_LINK_SCHEMA)),
    }
)

_VIDEO_PROPERTIES_SCHEMA = _strict_object(
    {
        "autoPlay": _nullable_boolean("Whether the video plays automatically."),
        "end": _nullable_integer("Video end time in seconds."),
        "mute": _nullable_boolean("Whether the video is muted."),
        "outline": _nullable(deepcopy(_OUTLINE_SCHEMA)),
        "start": _nullable_integer("Video start time in seconds."),
    }
)

_LINE_PROPERTIES_SCHEMA = _strict_object(
    {
        "lineFill": _nullable(deepcopy(_LINE_FILL_SCHEMA)),
        "weight": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "dashStyle": _nullable_string("Line dash style."),
        "startArrow": _nullable_string("Line start arrow."),
        "endArrow": _nullable_string("Line end arrow."),
        "startConnection": _nullable(deepcopy(_LINE_CONNECTION_SCHEMA)),
        "endConnection": _nullable(deepcopy(_LINE_CONNECTION_SCHEMA)),
        "link": _nullable(deepcopy(_LINK_SCHEMA)),
    }
)

_SHAPE_PROPERTIES_SCHEMA = _strict_object(
    {
        "autofit": _nullable(deepcopy(_AUTOFIT_SCHEMA)),
        "contentAlignment": _nullable_string("Content alignment, for example MIDDLE."),
        "shapeBackgroundFill": _nullable(
            _strict_object(
                {
                    "propertyState": _nullable_string(
                        "Shape background property state."
                    ),
                    "solidFill": _nullable(deepcopy(_SOLID_FILL_SCHEMA)),
                }
            )
        ),
        "outline": _nullable(deepcopy(_OUTLINE_SCHEMA)),
        "shadow": _nullable(deepcopy(_SHADOW_SCHEMA)),
        "link": _nullable(deepcopy(_LINK_SCHEMA)),
    }
)

_PAGE_PROPERTIES_SCHEMA = _strict_object(
    {
        "colorScheme": _nullable(
            _strict_object(
                {
                    "colors": _nullable(
                        _array_items(
                            _strict_object(
                                {
                                    "color": _nullable(deepcopy(_RGB_COLOR_SCHEMA)),
                                    "type": _nullable_string("Theme color type."),
                                }
                            )
                        )
                    ),
                }
            )
        ),
        "pageBackgroundFill": _nullable(
            _strict_object(
                {
                    "propertyState": _nullable_string(
                        "Page background property state."
                    ),
                    "solidFill": _nullable(deepcopy(_SOLID_FILL_SCHEMA)),
                    "stretchedPictureFill": _nullable(
                        _strict_object(
                            {
                                "contentUrl": _nullable_string("Picture content URL."),
                                "size": _nullable(deepcopy(_SIZE_SCHEMA)),
                            }
                        )
                    ),
                }
            )
        ),
    }
)

_SLIDE_LAYOUT_REFERENCE_SCHEMA = _strict_object(
    {
        "predefinedLayout": _nullable_string(
            "Predefined layout, for example BLANK or TITLE_AND_BODY."
        ),
        "layoutId": _nullable_string("Existing layout object ID."),
    }
)

_CELL_LOCATION_SCHEMA = _strict_object(
    {
        "rowIndex": {"type": "integer", "description": "Zero-based row index."},
        "columnIndex": {"type": "integer", "description": "Zero-based column index."},
    }
)

_TABLE_RANGE_SCHEMA = _strict_object(
    {
        "location": _nullable(deepcopy(_CELL_LOCATION_SCHEMA)),
        "rowSpan": _nullable_integer("Number of table rows in the range."),
        "columnSpan": _nullable_integer("Number of table columns in the range."),
    }
)

_TABLE_CELL_PROPERTIES_SCHEMA = _strict_object(
    {
        "tableCellBackgroundFill": _nullable(
            _strict_object(
                {
                    "solidFill": _nullable(deepcopy(_OPTIONAL_COLOR_SCHEMA)),
                }
            )
        ),
        "contentAlignment": _nullable_string("Table cell content alignment."),
    }
)

_TABLE_BORDER_PROPERTIES_SCHEMA = _strict_object(
    {
        "tableBorderFill": _nullable(deepcopy(_LINE_FILL_SCHEMA)),
        "weight": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "dashStyle": _nullable_string("Table border dash style."),
    }
)

_TABLE_COLUMN_PROPERTIES_SCHEMA = _strict_object(
    {
        "columnWidth": _nullable(deepcopy(_DIMENSION_SCHEMA)),
    }
)

_TABLE_ROW_PROPERTIES_SCHEMA = _strict_object(
    {
        "minRowHeight": _nullable(deepcopy(_DIMENSION_SCHEMA)),
    }
)

_CONTAINS_TEXT_SCHEMA = _strict_object(
    {
        "text": {"type": "string", "description": "Text to match."},
        "matchCase": _nullable_boolean("Whether matching is case-sensitive."),
    }
)

_PLACEHOLDER_SCHEMA = _strict_object(
    {
        "type": _nullable_string("Placeholder type, for example TITLE or BODY."),
        "index": _nullable_integer("Placeholder index."),
        "parentObjectId": _nullable_string("Parent placeholder object ID."),
    }
)

_LAYOUT_PLACEHOLDER_ID_MAPPING_SCHEMA = _strict_object(
    {
        "objectId": _nullable_string(
            "Object ID for the placeholder created on the slide."
        ),
        "layoutPlaceholder": _nullable(deepcopy(_PLACEHOLDER_SCHEMA)),
        "layoutPlaceholderObjectId": _nullable_string(
            "Object ID of the placeholder on the layout."
        ),
    }
)

_SLIDE_PROPERTIES_SCHEMA = _strict_object(
    {
        "isSkipped": _nullable_boolean(
            "Whether the slide is skipped in presentation mode."
        ),
        "layoutObjectId": _nullable_string("Layout object ID."),
        "masterObjectId": _nullable_string("Master object ID."),
    }
)

_PARAGRAPH_STYLE_SCHEMA = _strict_object(
    {
        "lineSpacing": _nullable_number("Paragraph line spacing."),
        "alignment": _nullable_string("Paragraph alignment."),
        "indentStart": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "indentEnd": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "spaceAbove": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "spaceBelow": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "indentFirstLine": _nullable(deepcopy(_DIMENSION_SCHEMA)),
        "direction": _nullable_string("Paragraph direction."),
        "spacingMode": _nullable_string("Paragraph spacing mode."),
    }
)


def _slides_request_variant(
    request_type: str, payload_properties: Dict[str, Any]
) -> Dict[str, Any]:
    return _strict_object(
        {
            request_type: _strict_object(payload_properties),
        }
    )


_SLIDES_BATCH_REQUEST_PAYLOAD_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "createSlide": {
        "objectId": _nullable_string("Optional slide object ID."),
        "insertionIndex": _nullable_integer("Optional slide insertion index."),
        "slideLayoutReference": _nullable(deepcopy(_SLIDE_LAYOUT_REFERENCE_SCHEMA)),
        "placeholderIdMappings": _nullable(
            _array_items(deepcopy(_LAYOUT_PLACEHOLDER_ID_MAPPING_SCHEMA))
        ),
    },
    "createShape": {
        "objectId": _nullable_string("Optional new shape object ID."),
        "shapeType": {"type": "string", "description": "Shape type."},
        "elementProperties": deepcopy(_ELEMENT_PROPERTIES_SCHEMA),
    },
    "createTable": {
        "objectId": _nullable_string("Optional new table object ID."),
        "elementProperties": deepcopy(_ELEMENT_PROPERTIES_SCHEMA),
        "rows": {"type": "integer", "description": "Number of table rows."},
        "columns": {"type": "integer", "description": "Number of table columns."},
    },
    "insertText": {
        "objectId": {
            "type": "string",
            "description": "Text-capable shape or table object ID.",
        },
        "insertionIndex": {"type": "integer", "description": "Text insertion index."},
        "text": {"type": "string", "description": "Text to insert."},
        "cellLocation": _nullable(deepcopy(_CELL_LOCATION_SCHEMA)),
    },
    "insertTableRows": {
        "tableObjectId": {"type": "string", "description": "Table object ID."},
        "cellLocation": deepcopy(_CELL_LOCATION_SCHEMA),
        "insertBelow": _nullable_boolean("Whether to insert rows below the cell."),
        "number": {"type": "integer", "description": "Number of rows to insert."},
    },
    "insertTableColumns": {
        "tableObjectId": {"type": "string", "description": "Table object ID."},
        "cellLocation": deepcopy(_CELL_LOCATION_SCHEMA),
        "insertRight": _nullable_boolean("Whether to insert columns to the right."),
        "number": {"type": "integer", "description": "Number of columns to insert."},
    },
    "deleteTableRow": {
        "tableObjectId": {"type": "string", "description": "Table object ID."},
        "cellLocation": deepcopy(_CELL_LOCATION_SCHEMA),
    },
    "deleteTableColumn": {
        "tableObjectId": {"type": "string", "description": "Table object ID."},
        "cellLocation": deepcopy(_CELL_LOCATION_SCHEMA),
    },
    "replaceAllText": {
        "containsText": deepcopy(_CONTAINS_TEXT_SCHEMA),
        "replaceText": {"type": "string", "description": "Replacement text."},
        "pageObjectIds": _nullable(
            _string_array("Slide/page object IDs to limit replacement.")
        ),
    },
    "deleteObject": {
        "objectId": {"type": "string", "description": "Object ID to delete."},
    },
    "updatePageElementTransform": {
        "objectId": {"type": "string", "description": "Page element ID."},
        "transform": deepcopy(_AFFINE_TRANSFORM_SCHEMA),
        "applyMode": _nullable_string("Apply mode, for example ABSOLUTE or RELATIVE."),
    },
    "updateSlidesPosition": {
        "slideObjectIds": _string_array("Slide object IDs to reposition."),
        "insertionIndex": {"type": "integer", "description": "Target insertion index."},
    },
    "deleteText": {
        "objectId": {"type": "string", "description": "Shape or table object ID."},
        "textRange": deepcopy(_TEXT_RANGE_SCHEMA),
        "cellLocation": _nullable(deepcopy(_CELL_LOCATION_SCHEMA)),
    },
    "createImage": {
        "objectId": _nullable_string("Optional image object ID."),
        "url": {"type": "string", "description": "Image URL."},
        "elementProperties": deepcopy(_ELEMENT_PROPERTIES_SCHEMA),
    },
    "createVideo": {
        "objectId": _nullable_string("Optional video object ID."),
        "source": {
            "type": "string",
            "description": "Video source, for example YOUTUBE.",
        },
        "id": {"type": "string", "description": "Video source ID."},
        "elementProperties": deepcopy(_ELEMENT_PROPERTIES_SCHEMA),
    },
    "createSheetsChart": {
        "objectId": _nullable_string("Optional chart object ID."),
        "spreadsheetId": {
            "type": "string",
            "description": "Google Sheets spreadsheet ID.",
        },
        "chartId": {"type": "integer", "description": "Sheets chart ID."},
        "linkingMode": _nullable_string("Chart linking mode."),
        "elementProperties": deepcopy(_ELEMENT_PROPERTIES_SCHEMA),
    },
    "createLine": {
        "objectId": _nullable_string("Optional line object ID."),
        "elementProperties": deepcopy(_ELEMENT_PROPERTIES_SCHEMA),
        "lineCategory": _nullable_string("Deprecated line category."),
        "category": _nullable_string("Line category."),
    },
    "refreshSheetsChart": {
        "objectId": {"type": "string", "description": "Sheets chart object ID."},
    },
    "updateShapeProperties": {
        "objectId": {"type": "string", "description": "Shape object ID."},
        "shapeProperties": deepcopy(_SHAPE_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated shape property fields.",
        },
    },
    "updateImageProperties": {
        "objectId": {"type": "string", "description": "Image object ID."},
        "imageProperties": deepcopy(_IMAGE_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated image property fields.",
        },
    },
    "updateVideoProperties": {
        "objectId": {"type": "string", "description": "Video object ID."},
        "videoProperties": deepcopy(_VIDEO_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated video property fields.",
        },
    },
    "updatePageProperties": {
        "objectId": {"type": "string", "description": "Slide/page ID."},
        "pageProperties": deepcopy(_PAGE_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated page property fields.",
        },
    },
    "updateTableCellProperties": {
        "objectId": {"type": "string", "description": "Table object ID."},
        "tableRange": _nullable(deepcopy(_TABLE_RANGE_SCHEMA)),
        "tableCellProperties": deepcopy(_TABLE_CELL_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated table cell property fields.",
        },
    },
    "updateLineProperties": {
        "objectId": {"type": "string", "description": "Line object ID."},
        "lineProperties": deepcopy(_LINE_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated line property fields.",
        },
    },
    "createParagraphBullets": {
        "objectId": {"type": "string", "description": "Shape or table object ID."},
        "textRange": deepcopy(_TEXT_RANGE_SCHEMA),
        "bulletPreset": {"type": "string", "description": "Bullet preset."},
        "cellLocation": _nullable(deepcopy(_CELL_LOCATION_SCHEMA)),
    },
    "replaceAllShapesWithImage": {
        "containsText": deepcopy(_CONTAINS_TEXT_SCHEMA),
        "imageUrl": {"type": "string", "description": "Replacement image URL."},
        "imageReplaceMethod": _nullable_string("Image replace method."),
        "replaceMethod": _nullable_string("Image replace method."),
        "pageObjectIds": _nullable(
            _string_array("Slide/page object IDs to limit replacement.")
        ),
    },
    # DuplicateObjectRequest.objectIds is an arbitrary string-to-string map.
    # Keeping this schema strict and closed is more important than exposing that
    # single free-form field, since reopening the object would reintroduce the
    # empty-object failure mode in strict-schema clients.
    "duplicateObject": {
        "objectId": {"type": "string", "description": "Object ID to duplicate."},
    },
    "updateTextStyle": {
        "objectId": {"type": "string", "description": "Shape or table object ID."},
        "textRange": deepcopy(_TEXT_RANGE_SCHEMA),
        "style": deepcopy(_TEXT_STYLE_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated text style fields.",
        },
        "cellLocation": _nullable(deepcopy(_CELL_LOCATION_SCHEMA)),
    },
    "replaceAllShapesWithSheetsChart": {
        "containsText": deepcopy(_CONTAINS_TEXT_SCHEMA),
        "spreadsheetId": {
            "type": "string",
            "description": "Google Sheets spreadsheet ID.",
        },
        "chartId": {"type": "integer", "description": "Sheets chart ID."},
        "linkingMode": _nullable_string("Chart linking mode."),
        "pageObjectIds": _nullable(
            _string_array("Slide/page object IDs to limit replacement.")
        ),
    },
    "deleteParagraphBullets": {
        "objectId": {"type": "string", "description": "Shape or table object ID."},
        "textRange": deepcopy(_TEXT_RANGE_SCHEMA),
        "cellLocation": _nullable(deepcopy(_CELL_LOCATION_SCHEMA)),
    },
    "updateParagraphStyle": {
        "objectId": {"type": "string", "description": "Shape or table object ID."},
        "style": deepcopy(_PARAGRAPH_STYLE_SCHEMA),
        "textRange": deepcopy(_TEXT_RANGE_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated paragraph style fields.",
        },
        "cellLocation": _nullable(deepcopy(_CELL_LOCATION_SCHEMA)),
    },
    "updateTableBorderProperties": {
        "objectId": {"type": "string", "description": "Table object ID."},
        "tableRange": _nullable(deepcopy(_TABLE_RANGE_SCHEMA)),
        "borderPosition": _nullable_string("Table border position."),
        "tableBorderProperties": deepcopy(_TABLE_BORDER_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated table border fields.",
        },
    },
    "updateTableColumnProperties": {
        "objectId": {"type": "string", "description": "Table object ID."},
        "columnIndices": _nullable(_integer_array("Zero-based column indices.")),
        "tableColumnProperties": deepcopy(_TABLE_COLUMN_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated table column fields.",
        },
    },
    "updateTableRowProperties": {
        "objectId": {"type": "string", "description": "Table object ID."},
        "rowIndices": _nullable(_integer_array("Zero-based row indices.")),
        "tableRowProperties": deepcopy(_TABLE_ROW_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated table row fields.",
        },
    },
    "mergeTableCells": {
        "objectId": {"type": "string", "description": "Table object ID."},
        "tableRange": deepcopy(_TABLE_RANGE_SCHEMA),
    },
    "unmergeTableCells": {
        "objectId": {"type": "string", "description": "Table object ID."},
        "tableRange": deepcopy(_TABLE_RANGE_SCHEMA),
    },
    "groupObjects": {
        "groupObjectId": _nullable_string("Optional object ID for the new group."),
        "childrenObjectIds": _string_array("Page element object IDs to group."),
    },
    "ungroupObjects": {
        "objectIds": _string_array("Group object IDs to ungroup."),
    },
    "updatePageElementAltText": {
        "objectId": {"type": "string", "description": "Page element object ID."},
        "title": _nullable_string("Alt text title."),
        "description": _nullable_string("Alt text description."),
    },
    "replaceImage": {
        "imageObjectId": {"type": "string", "description": "Existing image object ID."},
        "imageReplaceMethod": _nullable_string("Image replace method."),
        "url": {"type": "string", "description": "Replacement image URL."},
    },
    "updateSlideProperties": {
        "objectId": {"type": "string", "description": "Slide object ID."},
        "slideProperties": deepcopy(_SLIDE_PROPERTIES_SCHEMA),
        "fields": {
            "type": "string",
            "description": "Comma-separated slide property fields.",
        },
    },
    "updatePageElementsZOrder": {
        "pageElementObjectIds": _string_array("Page element object IDs."),
        "operation": {"type": "string", "description": "Z-order operation."},
    },
    "updateLineCategory": {
        "objectId": {"type": "string", "description": "Line object ID."},
        "lineCategory": {"type": "string", "description": "Updated line category."},
    },
    "rerouteLine": {
        "objectId": {"type": "string", "description": "Line object ID."},
    },
}

SLIDES_BATCH_UPDATE_REQUESTS_JSON_SCHEMA_EXTRA = {
    "minItems": 1,
    "items": {
        "anyOf": [
            _slides_request_variant(request_type, deepcopy(payload_schema))
            for request_type, payload_schema in _SLIDES_BATCH_REQUEST_PAYLOAD_SCHEMAS.items()
        ]
    },
}


def _slides_batch_request_guidance() -> str:
    examples = ", ".join(_SLIDES_BATCH_REQUEST_EXAMPLES)
    return f"exactly one Slides request type such as {examples}"


def strip_null_values(value: Any, *, _depth: int = 0) -> Any:
    """Remove nulls emitted for strict-schema optional fields before Google API calls."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if item is None:
                continue
            cleaned_item = strip_null_values(item, _depth=_depth + 1)
            if _depth >= 2 and cleaned_item == {}:
                continue
            cleaned[key] = cleaned_item
        return cleaned

    if isinstance(value, list):
        cleaned_items = []
        for item in value:
            if item is None:
                continue
            cleaned_item = strip_null_values(item, _depth=_depth + 1)
            if _depth >= 2 and cleaned_item == {}:
                continue
            cleaned_items.append(cleaned_item)
        return cleaned_items

    return value


def validate_batch_update_requests(requests: List[Dict[str, Any]]) -> None:
    guidance = _slides_batch_request_guidance()
    if not requests:
        raise UserInputError(
            "Invalid Slides batch update request: requests must contain at least "
            f"one request object with {guidance}."
        )

    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}] must be an object containing {guidance}."
            )

        request_types = list(request)
        if len(request_types) != 1:
            if not request_types:
                problem = "is empty"
            else:
                problem = f"contains multiple fields ({', '.join(request_types)})"
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}] {problem}; it must contain {guidance}."
            )

        request_type = request_types[0]
        if request_type not in _SLIDES_BATCH_REQUEST_TYPES:
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}] has unsupported request type '{request_type}'. "
                f"It must contain {guidance}."
            )

        if not isinstance(request[request_type], dict):
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}].{request_type} must be an object for {guidance}."
            )


def _get_request_payload(request: Dict[str, Any], request_type: str) -> Dict[str, Any]:
    payload = request.get(request_type)
    return payload if isinstance(payload, dict) else {}


def _find_insert_text_targets(
    requests: List[Dict[str, Any]],
) -> List[Tuple[int, str]]:
    targets = []
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            continue
        object_id = _get_request_payload(request, "insertText").get("objectId")
        if isinstance(object_id, str) and object_id:
            targets.append((index, object_id))
    return targets


def _find_created_slide_ids(requests: List[Dict[str, Any]]) -> Set[str]:
    slide_ids = set()
    for request in requests:
        if not isinstance(request, dict):
            continue
        object_id = _get_request_payload(request, "createSlide").get("objectId")
        if isinstance(object_id, str) and object_id:
            slide_ids.add(object_id)
    return slide_ids


async def _get_presentation_slide_ids(service, presentation_id: str) -> Set[str]:
    result = await asyncio.to_thread(
        service.presentations()
        .get(
            presentationId=presentation_id,
            fields=_PRESENTATION_PAGE_ID_FIELDS,
        )
        .execute
    )
    page_ids = {
        page["objectId"]
        for page_type in ("slides", "masters", "layouts")
        for page in result.get(page_type, [])
        if isinstance(page.get("objectId"), str)
    }
    for slide in result.get("slides", []):
        notes_id = slide.get("slideProperties", {}).get("notesPage", {}).get("objectId")
        if isinstance(notes_id, str):
            page_ids.add(notes_id)
    notes_master = result.get("notesMaster")
    if isinstance(notes_master, dict) and isinstance(notes_master.get("objectId"), str):
        page_ids.add(notes_master["objectId"])
    return page_ids


async def validate_insert_text_targets(
    service, presentation_id: str, requests: List[Dict[str, Any]]
) -> None:
    insert_text_targets = _find_insert_text_targets(requests)
    if not insert_text_targets:
        return

    slide_ids = _find_created_slide_ids(requests)
    slide_ids.update(await _get_presentation_slide_ids(service, presentation_id))

    invalid_targets = [
        (index, object_id)
        for index, object_id in insert_text_targets
        if object_id in slide_ids
    ]
    if not invalid_targets:
        return

    invalid_refs = ", ".join(
        f"requests[{index}].insertText.objectId='{object_id}'"
        for index, object_id in invalid_targets
    )
    raise UserInputError(
        "Invalid Slides batch update request: "
        f"{invalid_refs} targets a slide/page object. The Slides API only allows "
        "insertText on text-capable shapes or table cells. Create a text box or "
        "shape first with createShape, set elementProperties.pageObjectId to the "
        "slide ID, then insertText into the new shape objectId. For existing "
        "content, call get_page and use a Shape or Table element ID, not the "
        "Page ID."
    )
