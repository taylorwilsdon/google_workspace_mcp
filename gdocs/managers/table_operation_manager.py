"""
Table Operation Manager

This module provides high-level table operations that orchestrate
multiple Google Docs API calls for complex table manipulations.
"""

import logging
import asyncio
from typing import List, Dict, Any, Tuple

from gdocs.docs_helpers import create_insert_table_request
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
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Creates a table and populates it with data in a reliable multi-step process.

        This method extracts the complex logic from create_table_with_data tool function.

        Args:
            document_id: ID of the document to update
            table_data: 2D list of strings for table content
            index: Position to insert the table
            bold_headers: Whether to make the first row bold

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
            await self._create_empty_table(document_id, index, rows, cols)

            # Step 2: Get fresh document structure to find actual cell positions
            fresh_tables = await self._get_document_tables(document_id)
            if not fresh_tables:
                return False, "Could not find table after creation", {}

            # Step 3: Populate each cell with proper index refreshing
            population_count = await self._populate_table_cells(
                document_id, table_data, bold_headers
            )

            metadata = {
                "rows": rows,
                "columns": cols,
                "populated_cells": population_count,
                "table_index": len(fresh_tables) - 1,
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
        self, document_id: str, index: int, rows: int, cols: int
    ) -> None:
        """Create an empty table at the specified index."""
        logger.debug(f"Creating {rows}x{cols} table at index {index}")

        await asyncio.to_thread(
            self.service.documents()
            .batchUpdate(
                documentId=document_id,
                body={"requests": [create_insert_table_request(index, rows, cols)]},
            )
            .execute
        )

    async def _get_document_tables(self, document_id: str) -> List[Dict[str, Any]]:
        """Get fresh document structure and extract table information."""
        doc = await asyncio.to_thread(
            self.service.documents().get(documentId=document_id).execute
        )
        return find_tables(doc)

    async def _populate_table_cells(
        self, document_id: str, table_data: List[List[str]], bold_headers: bool
    ) -> int:
        """
        Populate table cells with data, refreshing structure after each insertion.

        This prevents index shifting issues by getting fresh cell positions
        before each insertion.
        """
        population_count = 0

        for row_idx, row_data in enumerate(table_data):
            logger.debug(f"Processing row {row_idx}: {len(row_data)} cells")

            for col_idx, cell_text in enumerate(row_data):
                if not cell_text:  # Skip empty cells
                    continue

                try:
                    # CRITICAL: Refresh document structure before each insertion
                    success = await self._populate_single_cell(
                        document_id,
                        row_idx,
                        col_idx,
                        cell_text,
                        bold_headers and row_idx == 0,
                    )

                    if success:
                        population_count += 1
                        logger.debug(f"Populated cell ({row_idx},{col_idx})")
                    else:
                        logger.warning(f"Failed to populate cell ({row_idx},{col_idx})")

                except Exception as e:
                    logger.error(
                        f"Error populating cell ({row_idx},{col_idx}): {str(e)}"
                    )

        return population_count

    async def _populate_single_cell(
        self,
        document_id: str,
        row_idx: int,
        col_idx: int,
        cell_text: str,
        apply_bold: bool = False,
    ) -> bool:
        """
        Populate a single cell with text, with optional bold formatting.

        Returns True if successful, False otherwise.
        """
        try:
            # Get fresh table structure to avoid index shifting issues
            tables = await self._get_document_tables(document_id)
            if not tables:
                return False

            table = tables[-1]  # Use the last table (newly created one)
            cells = table.get("cells", [])

            # Bounds checking
            if row_idx >= len(cells) or col_idx >= len(cells[row_idx]):
                logger.error(f"Cell ({row_idx},{col_idx}) out of bounds")
                return False

            cell = cells[row_idx][col_idx]
            insertion_index = cell.get("insertion_index")

            if not insertion_index:
                logger.warning(f"No insertion_index for cell ({row_idx},{col_idx})")
                return False

            # Insert text
            await asyncio.to_thread(
                self.service.documents()
                .batchUpdate(
                    documentId=document_id,
                    body={
                        "requests": [
                            {
                                "insertText": {
                                    "location": {"index": insertion_index},
                                    "text": cell_text,
                                }
                            }
                        ]
                    },
                )
                .execute
            )

            # Apply bold formatting if requested
            if apply_bold:
                await self._apply_bold_formatting(
                    document_id, insertion_index, insertion_index + len(cell_text)
                )

            return True

        except Exception as e:
            logger.error(f"Failed to populate single cell: {str(e)}")
            return False

    async def _apply_bold_formatting(
        self, document_id: str, start_index: int, end_index: int
    ) -> None:
        """Apply bold formatting to a text range."""
        await asyncio.to_thread(
            self.service.documents()
            .batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {
                            "updateTextStyle": {
                                "range": {
                                    "startIndex": start_index,
                                    "endIndex": end_index,
                                },
                                "textStyle": {"bold": True},
                                "fields": "bold",
                            }
                        }
                    ]
                },
            )
            .execute
        )

    async def populate_existing_table(
        self,
        document_id: str,
        table_index: int,
        table_data: List[List[str]],
        clear_existing: bool = False,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Populate an existing table with data.

        Args:
            document_id: ID of the document
            table_index: Index of the table to populate (0-based)
            table_data: 2D list of data to insert
            clear_existing: Whether to clear existing content first

        Returns:
            Tuple of (success, message, metadata)
        """
        try:
            tables = await self._get_document_tables(document_id)
            if table_index >= len(tables):
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

            # Populate cells
            population_count = await self._populate_existing_table_cells(
                document_id, table_index, table_data
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

    async def _populate_existing_table_cells(
        self, document_id: str, table_index: int, table_data: List[List[str]]
    ) -> int:
        """Populate cells in an existing table."""
        population_count = 0

        for row_idx, row_data in enumerate(table_data):
            for col_idx, cell_text in enumerate(row_data):
                if not cell_text:
                    continue

                # Get fresh table structure for each cell
                tables = await self._get_document_tables(document_id)
                if table_index >= len(tables):
                    break

                table = tables[table_index]
                cells = table.get("cells", [])

                if row_idx >= len(cells) or col_idx >= len(cells[row_idx]):
                    continue

                cell = cells[row_idx][col_idx]

                # For existing tables, append to existing content
                cell_end = cell["end_index"] - 1  # Don't include cell end marker

                try:
                    await asyncio.to_thread(
                        self.service.documents()
                        .batchUpdate(
                            documentId=document_id,
                            body={
                                "requests": [
                                    {
                                        "insertText": {
                                            "location": {"index": cell_end},
                                            "text": cell_text,
                                        }
                                    }
                                ]
                            },
                        )
                        .execute
                    )
                    population_count += 1

                except Exception as e:
                    logger.error(
                        f"Failed to populate existing cell ({row_idx},{col_idx}): {str(e)}"
                    )

        return population_count
