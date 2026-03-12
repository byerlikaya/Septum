from __future__ import annotations

"""
Table and field extraction for structured documents (contracts, forms).

This module uses pdfplumber to detect tables and extract key-value pairs
from PDF documents. Extracted fields become FieldChunks with metadata,
enabling better retrieval for contract queries.
"""

from dataclasses import dataclass
import re
from typing import List, Optional, Tuple

import pdfplumber


@dataclass
class ExtractedField:
    """A key-value field extracted from a table or form."""
    
    label: str
    value: str
    page_number: int
    confidence: float = 1.0
    field_type: Optional[str] = None


@dataclass
class ExtractedTable:
    """A table extracted from a document."""
    
    rows: List[List[str]]
    page_number: int
    has_header: bool = False


class TableFieldExtractor:
    """Extract tables and key-value fields from PDFs using pdfplumber.
    
    This extractor is designed for contract documents with structured
    information (employee details, party information, terms, etc.).
    """

    # Regex pattern for detecting "Label : Value" or "Label: Value" lines
    # Language-agnostic: works for Turkish, English, etc.
    _FIELD_PATTERN = re.compile(
        r"^(.{2,80}?)\s*:\s*(.+?)$",
        re.MULTILINE | re.UNICODE
    )

    def extract_from_pdf(self, pdf_path: str) -> Tuple[List[ExtractedField], List[ExtractedTable]]:
        """Extract fields and tables from a PDF file.

        Parameters
        ----------
        pdf_path:
            Path to the PDF file to process.

        Returns
        -------
        Tuple[List[ExtractedField], List[ExtractedTable]]
            A tuple of (fields, tables) extracted from the document.
        """
        fields: List[ExtractedField] = []
        tables: List[ExtractedTable] = []

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Extract tables from page
                page_tables = page.extract_tables()
                for table_data in page_tables:
                    if not table_data or len(table_data) < 2:
                        continue
                    
                    # Check if first row looks like a header
                    has_header = self._looks_like_header(table_data[0])
                    
                    tables.append(
                        ExtractedTable(
                            rows=table_data,
                            page_number=page_num,
                            has_header=has_header,
                        )
                    )
                    
                    # Try to extract fields from table (key-value pairs)
                    table_fields = self._extract_fields_from_table(table_data, page_num)
                    fields.extend(table_fields)

                # Extract fields from plain text (non-table content)
                text = page.extract_text() or ""
                text_fields = self._extract_fields_from_text(text, page_num)
                fields.extend(text_fields)

        return fields, tables

    def _looks_like_header(self, row: List[str]) -> bool:
        """Check if a table row looks like a header row.
        
        Heuristic: all cells are short (<40 chars) and mostly non-numeric.
        """
        if not row:
            return False
        
        for cell in row:
            cell_str = str(cell or "").strip()
            if len(cell_str) > 40:
                return False
            if cell_str.isdigit():
                return False
        
        return True

    def _extract_fields_from_table(
        self,
        table_data: List[List[str]],
        page_num: int,
    ) -> List[ExtractedField]:
        """Extract key-value fields from a table.
        
        Looks for two-column tables where:
        - Column 1 contains labels (short text, no digits)
        - Column 2 contains values
        """
        fields: List[ExtractedField] = []
        
        for row in table_data:
            if len(row) != 2:
                continue
            
            label_cell = str(row[0] or "").strip()
            value_cell = str(row[1] or "").strip()
            
            if not label_cell or not value_cell:
                continue
            
            # Skip if label looks like a value (too long, has digits)
            if len(label_cell) > 80:
                continue
            
            # Detect field type from label (language-agnostic patterns)
            field_type = self._infer_field_type(label_cell)
            
            fields.append(
                ExtractedField(
                    label=label_cell,
                    value=value_cell,
                    page_number=page_num,
                    field_type=field_type,
                    confidence=0.9,
                )
            )
        
        return fields

    def _extract_fields_from_text(
        self,
        text: str,
        page_num: int,
    ) -> List[ExtractedField]:
        """Extract key-value pairs from plain text using regex.
        
        Matches pattern: "Label : Value" on separate or same line.
        """
        fields: List[ExtractedField] = []
        
        for match in self._FIELD_PATTERN.finditer(text):
            label = match.group(1).strip()
            value = match.group(2).strip()
            
            if not label or not value:
                continue
            
            # Skip if label is too generic or looks like sentence fragment
            if len(label.split()) > 10:
                continue
            
            field_type = self._infer_field_type(label)
            
            fields.append(
                ExtractedField(
                    label=label,
                    value=value,
                    page_number=page_num,
                    field_type=field_type,
                    confidence=0.85,
                )
            )
        
        return fields

    def _infer_field_type(self, label: str) -> Optional[str]:
        """Infer generic field type from label text (language-agnostic).
        
        Uses ONLY structural patterns without hardcoded language-specific terms.
        Field type inference is optional and used only for metadata enrichment.
        """
        label_lower = label.lower()
        
        # Structural pattern detection (NO language-specific keywords)
        # Detection based on character patterns, not vocabulary
        
        # Phone pattern: contains digits with common separators
        if re.search(r'\d[\d\s\-\(\)]{5,}', label):
            return "phone"
        
        # Email pattern: contains @ symbol
        if '@' in label_lower or 'e-mail' in label_lower.replace('-', ''):
            return "email"
        
        # ID/Number pattern: ends with "no" or "id" (ultra-common abbreviations)
        # or contains "#" symbol
        if label_lower.endswith(('no', 'id', 'no.', 'id.')) or '#' in label:
            return "identifier"
        
        # Date pattern: contains digits and slashes/dashes suggesting date format
        if re.search(r'\d{1,4}[/\-\.]\d{1,2}[/\-\.]\d{1,4}', label):
            return "date"
        
        # Keep as generic for other cases
        # Field type is optional metadata, not required for functionality
        return None
