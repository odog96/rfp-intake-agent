"""Document parsing with fidelity ladder and auto-escalation."""

from rfp_intake.ingest.models import PageText, ParsedDoc, ParserMetadata, QualityMetrics
from rfp_intake.ingest.protocol import Parser

__all__ = [
    "PageText",
    "ParsedDoc",
    "Parser",
    "ParserMetadata",
    "QualityMetrics",
]
