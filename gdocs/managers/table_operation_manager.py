"""
Table Operation Manager

This module provides high-level table operations that orchestrate
multiple Google Docs API calls for complex table manipulations.
"""

import logging
import asyncio
from typing import List, Dict, Any, Tuple, Optional

from gdocs.docs_helpers import create_insert_table_request, create_insert_text_request
from gdocs.docs_structure import find_tables
from gdocs.docs_tables import validate_table_data

logger = logging.getLogger(__name__)


class TableOperationManager:
    """
    High-level manager for Google Docs table operations.

    Handles complex multi-step table operations including:
    - Creating tables with data population
    - Populating existing tables
    - Managing cell-by-cell operations with proper index refreshing
    """

    def __init__(self, service):
        """
        Initialize the table operation manager.

        Args:
            service: Google Docs API service instance
        """
        self.service = service

    async def create_and_populate_table(
        self,
        document_id: str,
        table_data: List[List[str]],
        index: int,
        bold_headers: bool = True,
        tab_id: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Creates a table and populates it with data in a reliable multi-step process.

        This method extracts the complex logic from create_table_with_data tool function.

        Args:
            document_id: ID of the document to update
            table_data: 2D list of strings for table content
            index: Position to insert the table
            bold_headers: Whether to make the first row bold
            tab_id: Optional tab ID for targeting a specific tab

        Returns:
            Tuple of (success, message, metadata)
        """
        logger.debug(
            f"Creating table at index {index}, dimensions: {len(table_data)}x{len(table_data[0]) if table_data and len(table_data) > 0 else 0}"
        )

        # Validate input data
        is_valid, error_msg = validate_table_data(table_data)
        if not is_valid:
            return False, f"Invalid table data: {error_msg}", {}

        rows = len(table_data)
        cols = len(table_data[0])

        try:
            # Step 1: Create empty table
            await self._create_empty_table(document_id, index, rows, cols, tab_id)

            # Step 2: Get fresh document structure to find actual cell positions
            fresh_tables = await self._get_document_tables(document_id, tab_id)
            if not fresh_tables:
                return False, "Could not find table after creation", {}

            # Step 3: Find the newly created table by insertion index
            # The table should be at or near the requested index
            target_table = self._find_table_near_index(fresh_tables, index)
            if not target_table:
                return (
                    False,
                    f"Could not locate newly created table near index {index}",
                    {},
                )

            # Step 4: Populate all cells in a single batch operation
            population_count = await self._populate_table_cells_batch(
                document_id, target_table, table_data, bold_headers, tab_id
            )

            metadata = {
                "rows": rows,
                "columns": cols,
                "populated_cells": population_count,
                "total_cells": rows * cols,
            }

            return (
                True,
                f"Successfully created {rows}x{cols} table and populated {population_count} cells",
                metadata,
            )

        except Exception as e:
            logger.error(f"Failed to create and populate table: {str(e)}")
            return False, f"Table creation failed: {str(e)}", {}

    async def _create_empty_table(
        self,
        document_id: str,
        index: int,
        rows: int,
        cols: int,
        tab_id: Optional[str] = None,
    ) -> None:
        """Create an empty table at the specified index."""
        logger.debug(f"Creating {rows}x{cols} table at index {index}")

        await asyncio.to_thread(
            self.service.documents()
            .batchUpdate(
                documentId=document_id,
                body={
                    "requests": [create_insert_table_request(index, rows, cols, tab_id)]
                },
            )
            .execute
        )

    async def _get_document_tables(
        self, document_id: str, tab_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get fresh document structure and extract table information."""
        doc = await asyncio.to_thread(
            self.service.documents()
            .get(documentId=document_id, includeTabsContent=True)
            .execute
        )

        if tab_id:
            tab = self._find_tab(doc.get("tabs", []), tab_id)
            if tab and "documentTab" in tab:
                doc = doc.copy()
                doc["body"] = tab["documentTab"].get("body", {})

        return find_tables(doc)

    @staticmethod
    def _find_tab(tabs: list, target_id: str):
        """Recursively find a tab by ID."""
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == target_id:
                return tab
            if "childTabs" in tab:
                found = TableOperationManager._find_tab(tab["childTabs"], target_id)
                if found:
                    return found
        return None

    @staticmethod
    def _find_table_near_index(
        tables: List[Dict[str, Any]], target_index: int
    ) -> Optional[Dict[str, Any]]:
        """
        Find the table whose start_index is closest to the target insertion index.

        When a table is inserted at an index, the table's actual start_index
        will be at or very near the requested index. We find the best match
        rather than blindly using tables[-1].
        """
        if not tables:
            return None

        best_match = None
        best_distance = float("inf")

        for table in tables:
            distance = abs(table["start_index"] - target_index)
            if distance < best_distance:
                best_distance = distance
                best_match = table

        # If the closest table is very far from the target, something is wrong.
        # Return None so the caller can fail cleanly rather than populate
        # the wrong table.
        if best_distance > 10:
            logger.warning(
                f"Closest table to index {target_index} is at {best_match['start_index']} "
                f"(distance={best_distance}). This may indicate an index mismatch."
            )
            return None

        return best_match

    async def _populate_table_cells_batch(
        self,
        document_id: str,
        table: Dict[str, Any],
        table_data: List[List[str]],
        bold_headers: bool,
        tab_id: Optional[str] = None,
    ) -> int:
        """
        Populate all table cells in a single batchUpdate call.

        Builds all insertText and updateTextStyle requests at once,
        processing cells in reverse document order to avoid index shifting.
        """
        cells = table.get("cells", [])
        if not cells:
            logger.warning("No cell information found in table")
            return 0

        requests = []
        population_count = 0

        # Build a list of (row_idx, col_idx, cell, text, should_bold) tuples
        # then sort by insertion_index descending so insertions don't shift later indices
        cell_operations = []

        for row_idx, row_data in enumerate(table_data):
            if row_idx >= len(cells):
                logger.warning(
                    f"Data has more rows ({len(table_data)}) than table ({len(cells)})"
                )
                break

            for col_idx, cell_text in enumerate(row_data):
                if col_idx >= len(cells[row_idx]):
                    logger.warning(
                        f"Data has more columns ({len(row_data)}) than table row {row_idx} ({len(cells[row_idx])})"
                    )
                    break

                if not cell_text:  # Skip empty cells
                    continue

                cell = cells[row_idx][col_idx]
                insertion_index = cell.get("insertion_index")

                if insertion_index is None:
                    logger.warning(
                        f"No insertion_index for cell ({row_idx},{col_idx}), skipping"
                    )
                    continue

                should_bold = bold_headers and row_idx == 0
                cell_operations.append(
                    (insertion_index, row_idx, col_idx, cell_text, should_bold)
                )

        # Sort by insertion_index descending — process last cells first
        # so that earlier insertions don't shift the indices of later ones
        cell_operations.sort(key=lambda x: x[0], reverse=True)

        for (
            insertion_index,
            row_idx,
            col_idx,
            cell_text,
            should_bold,
        ) in cell_operations:
            # Insert text using the helper that properly handles tab_id
            requests.append(
                create_insert_text_request(
                    index=insertion_index, text=cell_text, tab_id=tab_id
                )
            )

            # Apply bold formatting if this is a header cell
            if should_bold:
                style_request = {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": insertion_index,
                            "endIndex": insertion_index + len(cell_text),
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                }
                if tab_id:
                    style_request["updateTextStyle"]["range"]["tabId"] = tab_id
                requests.append(style_request)

            population_count += 1

        if not requests:
            logger.warning("No cell population requests generated")
            return 0

        logger.debug(
            f"Sending {len(requests)} requests to populate {population_count} cells"
        )

        # Execute all insertions in a single batchUpdate
        await asyncio.to_thread(
            self.service.documents()
            .batchUpdate(
                documentId=document_id,
                body={"requests": requests},
            )
            .execute
        )

        return population_count

    async def populate_existing_table(
        self,
        document_id: str,
        table_index: int,
        table_data: List[List[str]],
        tab_id: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Populate an existing table with data.

        Args:
            document_id: ID of the document
            table_index: Index of the table to populate (0-based)
            table_data: 2D list of data to insert
            tab_id: Optional tab ID for targeting a specific tab

        Returns:
            Tuple of (success, message, metadata)
        """
        try:
            tables = await self._get_document_tables(document_id, tab_id)
            if table_index < 0 or table_index >= len(tables):
                return (
                    False,
                    f"Table index {table_index} not found. Document has {len(tables)} tables",
                    {},
                )

            table_info = tables[table_index]

            # Validate dimensions
            table_rows = table_info["rows"]
            table_cols = table_info["columns"]
            data_rows = len(table_data)
            data_cols = len(table_data[0]) if table_data else 0

            if data_rows > table_rows or data_cols > table_cols:
                return (
                    False,
                    f"Data ({data_rows}x{data_cols}) exceeds table dimensions ({table_rows}x{table_cols})",
                    {},
                )

            # Populate cells using batch operation
            population_count = await self._populate_existing_table_cells_batch(
                document_id, table_info, table_data, tab_id
            )

            metadata = {
                "table_index": table_index,
                "populated_cells": population_count,
                "table_dimensions": f"{table_rows}x{table_cols}",
                "data_dimensions": f"{data_rows}x{data_cols}",
            }

            return (
                True,
                f"Successfully populated {population_count} cells in existing table",
                metadata,
            )

        except Exception as e:
            return False, f"Failed to populate existing table: {str(e)}", {}

    async def _populate_existing_table_cells_batch(
        self,
        document_id: str,
        table_info: Dict[str, Any],
        table_data: List[List[str]],
        tab_id: Optional[str] = None,
    ) -> int:
        """Populate cells in an existing table using a single batch operation."""
        cells = table_info.get("cells", [])
        if not cells:
            return 0

        requests = []
        population_count = 0

        # Build operations list sorted by index descending
        cell_operations = []

        for row_idx, row_data in enumerate(table_data):
            if row_idx >= len(cells):
                break

            for col_idx, cell_text in enumerate(row_data):
                if not cell_text:
                    continue

                if col_idx >= len(cells[row_idx]):
                    continue

                cell = cells[row_idx][col_idx]
                # For existing tables, insert at end of existing content
                cell_end = cell["end_index"] - 1  # Don't include cell end marker
                cell_operations.append((cell_end, cell_text))

        # Sort by index descending to avoid shifting
        cell_operations.sort(key=lambda x: x[0], reverse=True)

        for cell_end, cell_text in cell_operations:
            requests.append(
                create_insert_text_request(
                    index=cell_end, text=cell_text, tab_id=tab_id
                )
            )
            population_count += 1

        if requests:
            await asyncio.to_thread(
                self.service.documents()
                .batchUpdate(
                    documentId=document_id,
                    body={"requests": requests},
                )
                .execute
            )

        return population_count
