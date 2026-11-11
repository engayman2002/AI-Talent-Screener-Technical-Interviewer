"""Document Intelligence outbound port exports."""

from talent_screener.application.document_intelligence.ports.outbound import (
    candidate_profile_read_model_port as _read_mod,
)
from talent_screener.application.document_intelligence.ports.outbound import (
    hybrid_vector_search_port as _hybrid_mod,
)
from talent_screener.application.document_intelligence.ports.outbound.document_parser_port import (
    DocumentParserPort,
)

CandidateProfileReadModelPort = _read_mod.CandidateProfileReadModelPort
HybridVectorSearchPort = _hybrid_mod.HybridVectorSearchPort

__all__ = [
    "CandidateProfileReadModelPort",
    "DocumentParserPort",
    "HybridVectorSearchPort",
]
