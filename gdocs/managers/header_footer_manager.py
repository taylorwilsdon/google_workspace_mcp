"""
Header Footer Manager

This module provides high-level operations for managing headers and footers
in Google Docs, extracting complex logic from the main tools module.
"""

import logging
import asyncio
from typing import Any, Optional

from gdocs.docs_helpers import (
    create_create_header_footer_request,
    create_delete_range_request,
    create_insert_text_request,
)

logger = logging.getLogger(__name__)


class HeaderFooterManager:
    """
    High-level manager for Google Docs header and footer operations.

    Handles complex header/footer operations including:
    - Finding and updating existing headers/footers
    - Content replacement with proper range calculation
    - Section type management
    """

    def __init__(self, service):
        """
        Initialize the header footer manager.

        Args:
            service: Google Docs API service instance
        """
        self.service = service
        self._style_field_map = {
            "header": {
                "DEFAULT": "defaultHeaderId",
                "FIRST_PAGE_ONLY": "firstPageHeaderId",
                "EVEN_PAGE": "evenPageHeaderId",
            },
            "footer": {
                "DEFAULT": "defaultFooterId",
                "FIRST_PAGE_ONLY": "firstPageFooterId",
                "EVEN_PAGE": "evenPageFooterId",
            },
        }

    async def update_header_footer_content(
        self,
        document_id: str,
        section_type: str,
        content: str,
        header_footer_type: str = "DEFAULT",
    ) -> tuple[bool, str]:
        """
        Updates header or footer content in a document.

        This method extracts the complex logic from update_doc_headers_footers tool function.

        Args:
            document_id: ID of the document to update
            section_type: Type of section ("header" or "footer")
            content: New content for the section
            header_footer_type: Type of header/footer ("DEFAULT", "FIRST_PAGE_ONLY", "EVEN_PAGE")

        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Updating {section_type} in document {document_id}")

        # Validate section type
        if section_type not in ["header", "footer"]:
            return False, "section_type must be 'header' or 'footer'"

        # Validate header/footer type
        if header_footer_type not in ["DEFAULT", "FIRST_PAGE_ONLY", "EVEN_PAGE"]:
            return (
                False,
                "header_footer_type must be 'DEFAULT', 'FIRST_PAGE_ONLY', or 'EVEN_PAGE'",
            )

        try:
            # Get document structure
            doc = await self._get_document(document_id)
            target_doc, active_tab_id = self._get_target_doc_for_header_footer(doc)

            # Find the target section
            target_section, section_id = await self._find_target_section(
                target_doc, section_type, header_footer_type
            )

            if not section_id:
                created_id = await self._create_missing_section(
                    document_id, section_type, header_footer_type
                )
                if not created_id:
                    return (
                        False,
                        f"No {section_type} found in document and automatic creation failed",
                    )
                doc = await self._get_document(document_id)
                target_doc, active_tab_id = self._get_target_doc_for_header_footer(doc)
                target_section = (
                    target_doc.get("headers", {}).get(created_id)
                    if section_type == "header"
                    else target_doc.get("footers", {}).get(created_id)
                )
                section_id = created_id

            # Update the content
            success, replace_message = await self._replace_section_content(
                document_id, target_section, content, section_id, active_tab_id
            )

            if not success:
                # Retry once after re-fetching the document. Newly created headers/footers
                # can lag briefly before their segment content becomes fully visible.
                refreshed_doc = await self._get_document(document_id)
                refreshed_target_doc, refreshed_tab_id = (
                    self._get_target_doc_for_header_footer(refreshed_doc)
                )
                (
                    refreshed_section,
                    refreshed_section_id,
                ) = await self._find_target_section(
                    refreshed_target_doc, section_type, header_footer_type
                )
                if refreshed_section_id:
                    success, replace_message = await self._replace_section_content(
                        document_id,
                        refreshed_section,
                        content,
                        refreshed_section_id,
                        refreshed_tab_id,
                    )

            if success:
                return True, f"Updated {section_type} content in document {document_id}"
            else:
                return False, replace_message

        except Exception as e:
            logger.error(f"Failed to update {section_type}: {str(e)}")
            return False, f"Failed to update {section_type}: {str(e)}"

    async def _get_document(self, document_id: str) -> dict[str, Any]:
        """Get the full document data."""
        return await asyncio.to_thread(
            self.service.documents()
            .get(documentId=document_id, includeTabsContent=True)
            .execute
        )

    def _get_target_doc_for_header_footer(
        self, doc: dict[str, Any]
    ) -> tuple[dict[str, Any], Optional[str]]:
        """
        Return the document-like content container for header/footer operations.

        In tabbed docs, text content lives under tab.documentTab rather than the
        legacy top-level body/headers/footers/documentStyle fields.
        """
        tabs = doc.get("tabs", [])
        if not tabs:
            return doc, None

        flattened_tabs: list[dict[str, Any]] = []

        def add_tab_and_children(tab: dict[str, Any]) -> None:
            flattened_tabs.append(tab)
            for child in tab.get("childTabs", []):
                add_tab_and_children(child)

        for tab in tabs:
            add_tab_and_children(tab)

        for tab in flattened_tabs:
            document_tab = tab.get("documentTab")
            if not document_tab:
                continue
            tab_id = tab.get("tabProperties", {}).get("tabId")
            tab_doc = dict(document_tab)
            if "title" not in tab_doc:
                tab_doc["title"] = doc.get("title", "")
            return tab_doc, tab_id

        return doc, None

    async def _find_target_section(
        self, doc: dict[str, Any], section_type: str, header_footer_type: str
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """
        Find the target header or footer section.

        Args:
            doc: Document data
            section_type: "header" or "footer"
            header_footer_type: Type of header/footer

        Returns:
            Tuple of (section_data, section_id) or (None, None) if not found
        """
        if section_type == "header":
            sections = doc.get("headers", {})
        else:
            sections = doc.get("footers", {})

        style_section_id = self._resolve_section_id_from_styles(
            doc, section_type, header_footer_type
        )
        if style_section_id:
            return sections.get(style_section_id), style_section_id

        # First, try to find an exact match based on common patterns
        for section_id, section_data in sections.items():
            # Check if section_data contains type information
            if "type" in section_data and section_data["type"] == header_footer_type:
                return section_data, section_id

        # If no exact match, try pattern matching on section ID
        # Google Docs often uses predictable section ID patterns
        target_patterns = {
            "DEFAULT": ["default", "kix"],  # DEFAULT headers often have these patterns
            "FIRST_PAGE": ["first", "firstpage"],
            "EVEN_PAGE": ["even", "evenpage"],
            "FIRST_PAGE_ONLY": ["first", "firstpage"],  # Legacy support
        }

        patterns = target_patterns.get(header_footer_type, [])
        for pattern in patterns:
            for section_id, section_data in sections.items():
                if pattern.lower() in section_id.lower():
                    return section_data, section_id

        # If still no match, return the first available section as fallback
        # This maintains backward compatibility
        for section_id, section_data in sections.items():
            return section_data, section_id

        return None, None

    def _resolve_section_id_from_styles(
        self, doc: dict[str, Any], section_type: str, header_footer_type: str
    ) -> Optional[str]:
        """Resolve a header/footer segment ID from document or section styles."""
        style_field = self._style_field_map.get(section_type, {}).get(
            header_footer_type
        )
        if not style_field:
            return None

        # SectionStyle overrides DocumentStyle for the first section.
        for element in doc.get("body", {}).get("content", []):
            section_style = element.get("sectionBreak", {}).get("sectionStyle", {})
            if not section_style:
                continue
            section_id = section_style.get(style_field)
            if section_id:
                return section_id
            break

        return doc.get("documentStyle", {}).get(style_field)

    async def _replace_section_content(
        self,
        document_id: str,
        section: dict[str, Any],
        new_content: str,
        section_id: str,
        tab_id: Optional[str],
    ) -> tuple[bool, str]:
        """
        Replace the content in a header or footer section.

        Args:
            document_id: Document ID
            section: Section data containing content elements
            new_content: New content to insert

        Returns:
            Tuple of (success, message)
        """
        content_elements = section.get("content", []) if section else []
        first_para = self._find_first_paragraph(content_elements)
        has_meaningful_content = self._segment_has_meaningful_content(content_elements)
        if first_para:
            start_index = first_para.get("startIndex", 0)
            end_index = first_para.get("endIndex", 0)
        else:
            start_index = 0
            end_index = 0

        # Build requests to replace content
        requests = []

        # Delete existing content if any (preserve paragraph structure)
        # Docs segments often contain a single empty paragraph marker. Deleting
        # start..end-1 in that case produces an empty range, which the API rejects.
        if has_meaningful_content and end_index - start_index > 1:
            requests.append(
                create_delete_range_request(
                    start_index,
                    end_index - 1,  # Preserve the trailing paragraph marker
                    tab_id=tab_id,
                    segment_id=section_id,
                )
            )

        # Insert new content
        if first_para and has_meaningful_content:
            requests.append(
                create_insert_text_request(
                    start_index,
                    new_content,
                    tab_id=tab_id,
                    segment_id=section_id,
                )
            )
        else:
            # Newly created or empty segments may not expose paragraph content yet.
            # Append directly to the segment so callers do not need a follow-up create.
            requests.append(
                create_insert_text_request(
                    None,
                    new_content,
                    tab_id=tab_id,
                    segment_id=section_id,
                    end_of_segment=True,
                )
            )

        try:
            await asyncio.to_thread(
                self.service.documents()
                .batchUpdate(documentId=document_id, body={"requests": requests})
                .execute
            )
            return True, "ok"

        except Exception as e:
            logger.error(f"Failed to replace section content: {str(e)}")
            return (
                False,
                f"Failed to write {section_id} segment content: {str(e)}",
            )

    def _segment_has_meaningful_content(
        self, content_elements: list[dict[str, Any]]
    ) -> bool:
        """
        Return True when a header/footer segment contains user-visible text beyond
        the default empty paragraph marker.
        """
        for element in content_elements:
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            for para_element in paragraph.get("elements", []):
                text_run = para_element.get("textRun")
                if not text_run:
                    continue
                content = text_run.get("content", "")
                if content.strip():
                    return True
        return False

    async def _create_missing_section(
        self, document_id: str, section_type: str, header_footer_type: str = "DEFAULT"
    ) -> Optional[str]:
        """Create a missing header/footer and return its new segment ID."""
        request = create_create_header_footer_request(section_type, header_footer_type)
        try:
            result = await asyncio.to_thread(
                self.service.documents()
                .batchUpdate(documentId=document_id, body={"requests": [request]})
                .execute
            )
        except Exception as e:
            if "already exists" in str(e).lower():
                try:
                    doc = await self._get_document(document_id)
                    return self._resolve_section_id_from_styles(
                        doc, section_type, header_footer_type
                    )
                except Exception:
                    pass
            logger.error(f"Failed to create missing {section_type}: {str(e)}")
            return None

        replies = result.get("replies", [])
        if not replies:
            return None

        reply = replies[0]
        if section_type == "header":
            return reply.get("createHeader", {}).get("headerId")
        return reply.get("createFooter", {}).get("footerId")

    def _find_first_paragraph(
        self, content_elements: list[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        """Find the first paragraph element in content."""
        for element in content_elements:
            if "paragraph" in element:
                return element
        return None

    async def get_header_footer_info(self, document_id: str) -> dict[str, Any]:
        """
        Get information about all headers and footers in the document.

        Args:
            document_id: Document ID

        Returns:
            Dictionary with header and footer information
        """
        try:
            doc = await self._get_document(document_id)

            headers_info = {}
            for header_id, header_data in doc.get("headers", {}).items():
                headers_info[header_id] = self._extract_section_info(header_data)

            footers_info = {}
            for footer_id, footer_data in doc.get("footers", {}).items():
                footers_info[footer_id] = self._extract_section_info(footer_data)

            return {
                "headers": headers_info,
                "footers": footers_info,
                "has_headers": bool(headers_info),
                "has_footers": bool(footers_info),
            }

        except Exception as e:
            logger.error(f"Failed to get header/footer info: {str(e)}")
            return {"error": str(e)}

    def _extract_section_info(self, section_data: dict[str, Any]) -> dict[str, Any]:
        """Extract useful information from a header/footer section."""
        content_elements = section_data.get("content", [])

        # Extract text content
        text_content = ""
        for element in content_elements:
            if "paragraph" in element:
                para = element["paragraph"]
                for para_element in para.get("elements", []):
                    if "textRun" in para_element:
                        text_content += para_element["textRun"].get("content", "")

        return {
            "content_preview": text_content[:100] if text_content else "(empty)",
            "element_count": len(content_elements),
            "start_index": content_elements[0].get("startIndex", 0)
            if content_elements
            else 0,
            "end_index": content_elements[-1].get("endIndex", 0)
            if content_elements
            else 0,
        }

    async def create_header_footer(
        self, document_id: str, section_type: str, header_footer_type: str = "DEFAULT"
    ) -> tuple[bool, str]:
        """
        Create a new header or footer section.

        Args:
            document_id: Document ID
            section_type: "header" or "footer"
            header_footer_type: Type of header/footer ("DEFAULT", "FIRST_PAGE", or "EVEN_PAGE")

        Returns:
            Tuple of (success, message)
        """
        if section_type not in ["header", "footer"]:
            return False, "section_type must be 'header' or 'footer'"

        # Map our type names to API type names
        type_mapping = {
            "DEFAULT": "DEFAULT",
            "FIRST_PAGE": "FIRST_PAGE",
            "EVEN_PAGE": "EVEN_PAGE",
            "FIRST_PAGE_ONLY": "FIRST_PAGE",  # Support legacy name
        }

        api_type = type_mapping.get(header_footer_type, header_footer_type)
        if api_type not in ["DEFAULT", "FIRST_PAGE", "EVEN_PAGE"]:
            return (
                False,
                "header_footer_type must be 'DEFAULT', 'FIRST_PAGE', or 'EVEN_PAGE'",
            )

        try:
            # Build the request
            request = {"type": api_type}

            # Create the appropriate request type
            if section_type == "header":
                batch_request = {"createHeader": request}
            else:
                batch_request = {"createFooter": request}

            # Execute the request
            await asyncio.to_thread(
                self.service.documents()
                .batchUpdate(documentId=document_id, body={"requests": [batch_request]})
                .execute
            )

            return True, f"Successfully created {section_type} with type {api_type}"

        except Exception as e:
            error_msg = str(e)
            if "already exists" in error_msg.lower():
                return (
                    False,
                    f"A {section_type} of type {api_type} already exists in the document",
                )
            return False, f"Failed to create {section_type}: {error_msg}"
