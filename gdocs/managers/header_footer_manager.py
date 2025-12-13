"""
Header Footer Manager

This module provides high-level operations for managing headers and footers
in Google Docs, extracting complex logic from the main tools module.
"""

import logging
import asyncio
from typing import Any, Optional

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

            # Find the target section
            target_section, section_id = await self._find_target_section(
                doc, section_type, header_footer_type
            )

            if not target_section:
                return (
                    False,
                    f"No {section_type} found in document. Please create a {section_type} first in Google Docs.",
                )

            # Update the content
            success = await self._replace_section_content(
                document_id, target_section, content
            )

            if success:
                return True, f"Updated {section_type} content in document {document_id}"
            else:
                return (
                    False,
                    f"Could not find content structure in {section_type} to update",
                )

        except Exception as e:
            logger.error(f"Failed to update {section_type}: {str(e)}")
            return False, f"Failed to update {section_type}: {str(e)}"

    async def _get_document(self, document_id: str) -> dict[str, Any]:
        """Get the full document data."""
        return await asyncio.to_thread(
            self.service.documents().get(documentId=document_id).execute
        )

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

        # Try to match section based on header_footer_type
        # Google Docs API typically uses section IDs that correspond to types

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

    async def _replace_section_content(
        self, document_id: str, section: dict[str, Any], new_content: str
    ) -> bool:
        """
        Replace the content in a header or footer section.

        Args:
            document_id: Document ID
            section: Section data containing content elements
            new_content: New content to insert

        Returns:
            True if successful, False otherwise
        """
        content_elements = section.get("content", [])
        if not content_elements:
            return False

        # Find the first paragraph to replace content
        first_para = self._find_first_paragraph(content_elements)
        if not first_para:
            return False

        # Calculate content range
        start_index = first_para.get("startIndex", 0)
        end_index = first_para.get("endIndex", 0)

        # Build requests to replace content
        requests = []

        # Delete existing content if any (preserve paragraph structure)
        if end_index > start_index:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": start_index,
                            "endIndex": end_index - 1,  # Keep the paragraph end marker
                        }
                    }
                }
            )

        # Insert new content
        requests.append(
            {"insertText": {"location": {"index": start_index}, "text": new_content}}
        )

        try:
            await asyncio.to_thread(
                self.service.documents()
                .batchUpdate(documentId=document_id, body={"requests": requests})
                .execute
            )
            return True

        except Exception as e:
            logger.error(f"Failed to replace section content: {str(e)}")
            return False

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
