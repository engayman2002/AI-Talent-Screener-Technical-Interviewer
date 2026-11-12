"""Composition Root package exports."""

from talent_screener.composition_root.container import (
    ApplicationContainer,
    ExternalAdapterRegistry,
    Phase3PipelineBundle,
    Phase4PipelineBundle,
    Phase5PipelineBundle,
    Phase6PipelineBundle,
    RepositoryRegistry,
    build_in_memory_repositories,
    build_phase3_container,
    build_phase3_external_adapters,
    build_phase4_container,
    build_phase4_external_adapters,
    build_phase5_container,
    build_phase6_container,
    build_sql_repositories,
)
from talent_screener.composition_root.protocols import (
    ApplicationContainerPort,
    EventChoreographyWiringPort,
    UseCaseCatalogPort,
)

__all__ = [
    "ApplicationContainer",
    "ApplicationContainerPort",
    "EventChoreographyWiringPort",
    "ExternalAdapterRegistry",
    "Phase3PipelineBundle",
    "Phase4PipelineBundle",
    "Phase5PipelineBundle",
    "Phase6PipelineBundle",
    "RepositoryRegistry",
    "UseCaseCatalogPort",
    "build_in_memory_repositories",
    "build_phase3_container",
    "build_phase3_external_adapters",
    "build_phase4_container",
    "build_phase4_external_adapters",
    "build_phase5_container",
    "build_phase6_container",
    "build_sql_repositories",
]
