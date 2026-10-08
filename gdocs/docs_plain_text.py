"""Context-preserving plain-text renderer for Google Docs API responses."""

from __future__ import annotations

import logging
import re
from typing import Any

from gdocs.docs_links import LinkTarget, resolve_link_target, text_run_link

logger = logging.getLogger(__name__)

TAB_HEADER_FORMAT = "\n--- TAB: {tab_name} (ID: {tab_id}) ---\n"
_TRAILING_NEWLINES = re.compile(r"(\n+)$")
_STRUCTURAL_METADATA_KEYS = {
    "startIndex",
    "endIndex",
    "suggestedInsertionIds",
    "suggestedDeletionIds",
}
_PARAGRAPH_METADATA_KEYS = _STRUCTURAL_METADATA_KEYS | {
    "suggestedTextStyleChanges",
}


def render_doc_to_plain_text(
    doc: dict[str, Any], current_tab_id: str | None = None
) -> str:
    """Render a Docs API document without discarding semantic context.

    The output intentionally remains plain text. Existing paragraph and tab
    marker behavior is retained, while API-only structures receive readable
    annotations instead of disappearing.
    """
    renderer = _PlainTextRenderer()
    return renderer.render_document(doc, current_tab_id)


class _PlainTextRenderer:
    """Render semantic annotations using each document tab's own metadata."""

    def render_document(
        self, doc: dict[str, Any], current_tab_id: str | None = None
    ) -> str:
        """Combine legacy body content and nested tabs without dropping whitespace."""
        sections: list[str] = []

        main_content = self._render_context(doc, current_tab_id=current_tab_id)
        if main_content:
            sections.append(main_content)

        for tab in doc.get("tabs", []):
            sections.append(self._render_tab(tab))

        return "".join(sections)

    def _render_tab(self, tab: dict[str, Any], level: int = 0) -> str:
        """Label a tab and recursively render its children with indented titles."""
        result: list[str] = []
        document_tab = tab.get("documentTab")
        if document_tab is not None:
            props = tab.get("tabProperties", {})
            title = props.get("title", "Untitled Tab")
            tab_id = props.get("tabId", "Unknown ID")
            indented_title = f"{'    ' * level}{title}"
            result.append(
                TAB_HEADER_FORMAT.format(tab_name=indented_title, tab_id=tab_id)
            )
            result.append(self._render_context(document_tab, current_tab_id=tab_id))

        for child in tab.get("childTabs", []):
            result.append(self._render_tab(child, level + 1))
        return "".join(result)

    def _render_context(
        self, context: dict[str, Any], current_tab_id: str | None
    ) -> str:
        """Render body and segments, then list positioned objects lacking anchors."""
        referenced_positioned: set[str] = set()
        parts: list[str] = []

        body = context.get("body", {})
        parts.append(
            self._render_segment(
                body.get("content", []),
                context,
                current_tab_id,
                referenced_positioned,
            )
        )

        for header_id, header in context.get("headers", {}).items():
            parts.append(f"\n--- HEADER: {header_id} ---\n")
            parts.append(
                self._render_segment(
                    header.get("content", []),
                    context,
                    current_tab_id,
                    referenced_positioned,
                )
            )

        for footer_id, footer in context.get("footers", {}).items():
            parts.append(f"\n--- FOOTER: {footer_id} ---\n")
            parts.append(
                self._render_segment(
                    footer.get("content", []),
                    context,
                    current_tab_id,
                    referenced_positioned,
                )
            )

        positioned_objects = context.get("positionedObjects", {})
        unresolved = [
            object_id
            for object_id in positioned_objects
            if object_id not in referenced_positioned
        ]
        if unresolved:
            parts.append("\n--- UNRESOLVED POSITIONED OBJECTS ---\n")
            for object_id in unresolved:
                marker = self._render_object(
                    object_id,
                    positioned_objects,
                    object_kind="Positioned object",
                )
                parts.append(f"{object_id}: {marker}\n")

        return "".join(parts)

    def _render_segment(
        self,
        content: list[dict[str, Any]],
        context: dict[str, Any],
        current_tab_id: str | None,
        referenced_positioned: set[str],
        active_footnotes: set[str] | None = None,
    ) -> str:
        """Append referenced footnotes while preventing cycles in nested references."""
        footnote_refs: list[str] = []
        rendered = self._render_elements(
            content,
            context,
            current_tab_id,
            referenced_positioned,
            footnote_refs,
        )

        active = set(active_footnotes or ())
        for footnote_id in footnote_refs:
            if footnote_id in active:
                continue
            footnote = context.get("footnotes", {}).get(footnote_id)
            if not footnote:
                continue
            nested_active = active | {footnote_id}
            footnote_text = self._render_segment(
                footnote.get("content", []),
                context,
                current_tab_id,
                referenced_positioned,
                active_footnotes=nested_active,
            ).strip()
            if rendered and not rendered.endswith("\n"):
                rendered += "\n"
            rendered += f"Footnote {footnote_id}: {footnote_text}\n"

        return rendered

    def _render_elements(
        self,
        elements: list[dict[str, Any]],
        context: dict[str, Any],
        current_tab_id: str | None,
        referenced_positioned: set[str],
        footnote_refs: list[str],
    ) -> str:
        """Traverse structural elements and collect object and footnote references."""
        parts: list[str] = []
        for position, element in enumerate(elements):
            if "paragraph" in element:
                paragraph = self._render_paragraph(
                    element.get("paragraph", {}),
                    context,
                    current_tab_id,
                    referenced_positioned,
                    footnote_refs,
                )
                if paragraph:
                    parts.append(paragraph)
            elif "table" in element:
                if parts and not parts[-1].endswith("\n"):
                    parts.append("\n")
                parts.append(
                    self._render_table(
                        element.get("table", {}),
                        context,
                        current_tab_id,
                        referenced_positioned,
                        footnote_refs,
                    )
                )
            elif "tableOfContents" in element:
                table_of_contents = element.get("tableOfContents", {})
                parts.append(
                    self._render_elements(
                        table_of_contents.get("content", []),
                        context,
                        current_tab_id,
                        referenced_positioned,
                        footnote_refs,
                    )
                )
            elif "sectionBreak" in element:
                # The Docs API opens every body with a section break; only later
                # ones reflect a break the author inserted.
                if position > 0:
                    parts.append("[Section Break]\n")
            else:
                element_type = self._variant_type(element, _STRUCTURAL_METADATA_KEYS)
                if element_type:
                    parts.append(self._unsupported_marker(element_type))
        return "".join(parts)

    def _render_paragraph(
        self,
        paragraph: dict[str, Any],
        context: dict[str, Any],
        current_tab_id: str | None,
        referenced_positioned: set[str],
        footnote_refs: list[str],
    ) -> str:
        """Preserve text runs and annotate chips, breaks, and anchored objects."""
        parts: list[str] = []
        elements = paragraph.get("elements", [])
        for position, element in enumerate(elements):
            if "textRun" in element:
                text_run = element.get("textRun", {})
                content = text_run.get("content", "").replace(
                    "\ue907", "[Smart Chip: details unavailable from Docs API]"
                )
                link = text_run.get("textStyle", {}).get("link")
                # Annotate a link split across runs once, after its final run.
                if link and text_run_link(elements, position + 1) == link:
                    parts.append(content)
                else:
                    parts.append(
                        self._render_linked_text(content, link, current_tab_id)
                    )
            elif "person" in element:
                parts.append(self._render_person(element.get("person", {})))
            elif "richLink" in element:
                parts.append(self._render_rich_link(element.get("richLink", {})))
            elif "dateElement" in element:
                parts.append(self._render_date(element.get("dateElement", {})))
            elif "inlineObjectElement" in element:
                object_id = element.get("inlineObjectElement", {}).get(
                    "inlineObjectId", ""
                )
                parts.append(
                    self._render_object(
                        object_id,
                        context.get("inlineObjects", {}),
                        object_kind="Inline object",
                    )
                )
            elif "positionedObjectElement" in element:
                object_id = element.get("positionedObjectElement", {}).get(
                    "positionedObjectId", ""
                )
                referenced_positioned.add(object_id)
                parts.append(
                    self._render_object(
                        object_id,
                        context.get("positionedObjects", {}),
                        object_kind="Positioned object",
                    )
                )
            elif "footnoteReference" in element:
                footnote_id = element.get("footnoteReference", {}).get("footnoteId", "")
                if footnote_id:
                    if footnote_id not in footnote_refs:
                        footnote_refs.append(footnote_id)
                    parts.append(f"[Footnote: {footnote_id}]")
            elif "pageBreak" in element:
                parts.append("[Page Break]")
            elif "columnBreak" in element:
                parts.append("[Column Break]")
            elif "horizontalRule" in element:
                parts.append("[Horizontal Rule]")
            elif "autoText" in element:
                auto_type = element.get("autoText", {}).get("type", "UNKNOWN")
                parts.append(f"[Auto text: {auto_type}]")
            elif "equation" in element:
                parts.append("[Equation]")
            else:
                element_type = self._variant_type(element, _PARAGRAPH_METADATA_KEYS)
                if element_type:
                    parts.append(self._unsupported_marker(element_type))

        text = "".join(parts)
        positioned_ids = paragraph.get("positionedObjectIds", [])
        if positioned_ids:
            trailing_newlines = ""
            match = _TRAILING_NEWLINES.search(text)
            if match:
                trailing_newlines = match.group(1)
                text = text[: match.start()]
            for object_id in positioned_ids:
                referenced_positioned.add(object_id)
                marker = self._render_object(
                    object_id,
                    context.get("positionedObjects", {}),
                    object_kind="Positioned object",
                )
                separator = " " if text and not text.endswith((" ", "\t")) else ""
                text += separator + marker
            text += trailing_newlines
        return text

    def _render_table(
        self,
        table: dict[str, Any],
        context: dict[str, Any],
        current_tab_id: str | None,
        referenced_positioned: set[str],
        footnote_refs: list[str],
    ) -> str:
        """Separate cells with tabs and rows with newlines, flattening nested content."""
        rows: list[str] = []
        for row in table.get("tableRows", []):
            cells: list[str] = []
            for cell in row.get("tableCells", []):
                cell_text = self._render_elements(
                    cell.get("content", []),
                    context,
                    current_tab_id,
                    referenced_positioned,
                    footnote_refs,
                ).strip()
                flattened_lines = [line.strip() for line in cell_text.splitlines()]
                cells.append(" ".join(line for line in flattened_lines if line))
            rows.append("\t".join(cells))
        return "\n".join(rows) + ("\n" if rows else "")

    def _render_linked_text(
        self,
        content: str,
        link: dict[str, Any] | None,
        current_tab_id: str | None,
    ) -> str:
        """Place link annotations before the text run's trailing newlines."""
        target = resolve_link_target(link)
        if not target or not content:
            return content

        trailing_newlines = ""
        match = _TRAILING_NEWLINES.search(content)
        if match:
            trailing_newlines = match.group(1)
            content = content[: match.start()]
        if not content:
            return trailing_newlines

        return self._annotate_link(content, target, current_tab_id) + trailing_newlines

    @staticmethod
    def _annotate_link(
        label: str, target: LinkTarget, current_tab_id: str | None
    ) -> str:
        """Append a readable destination, using the current tab for local targets."""
        if target.kind == "url":
            return f"{label} ({target.value})"
        if target.kind in ("heading", "bookmark"):
            tab_id = target.tab_id or current_tab_id
            tab_part = f", tab: {tab_id}" if tab_id else ""
            return f"{label} [{target.kind}: {target.value}{tab_part}]"
        if target.kind == "tab":
            return f"{label} [tab: {target.value}]"
        return f"{label} [link target unavailable]"

    @staticmethod
    def _render_person(person: dict[str, Any]) -> str:
        """Show a person's available name and email, or an explicit missing marker."""
        props = person.get("personProperties", {})
        name = props.get("name", "")
        email = props.get("email", "")
        if name and email:
            return f"{name} <{email}>"
        if name:
            return name
        if email:
            return f"<{email}>"
        return "[Person chip: details unavailable]"

    @staticmethod
    def _render_rich_link(rich_link: dict[str, Any]) -> str:
        """Retain a rich link's title and URI even when only one is available."""
        props = rich_link.get("richLinkProperties", {})
        title = props.get("title", "")
        uri = props.get("uri", "")
        if title and uri:
            return f"{title} ({uri})"
        if title:
            return title
        if uri:
            return uri
        return "[Rich link: details unavailable]"

    @staticmethod
    def _render_date(date_element: dict[str, Any]) -> str:
        """Prefer a date chip's display text, falling back to its timestamp."""
        props = date_element.get("dateElementProperties", {})
        return (
            props.get("displayText")
            or props.get("timestamp")
            or "[Date: details unavailable]"
        )

    @staticmethod
    def _render_object(
        object_id: str,
        objects: dict[str, Any],
        *,
        object_kind: str,
    ) -> str:
        """Describe an image from its metadata or identify an unresolved object."""
        obj = objects.get(object_id, {}) if object_id else {}
        properties_key = (
            "inlineObjectProperties"
            if object_kind == "Inline object"
            else "positionedObjectProperties"
        )
        embedded = obj.get(properties_key, {}).get("embeddedObject", {})
        title = embedded.get("title") or embedded.get("description") or ""
        image_properties = embedded.get("imageProperties", {})
        uri = image_properties.get("sourceUri")

        if title and uri:
            return f"[Image: {title}; URI: {uri}]"
        if title:
            return f"[Image: {title}]"
        if uri:
            return f"[Image; URI: {uri}]"
        identifier = object_id or "ID"
        return f"[{object_kind} {identifier}: details unavailable]"

    @staticmethod
    def _variant_type(element: dict[str, Any], ignored: set[str]) -> str | None:
        """Find the element variant while ignoring structural and suggestion fields."""
        return next((key for key in element if key not in ignored), None)

    @staticmethod
    def _unsupported_marker(element_type: str) -> str:
        """Log an unknown variant and retain a visible marker in the output."""
        logger.warning("Unsupported Google Docs element: %s", element_type)
        return f"[Unsupported Google Docs element: {element_type}]"
