"""Composition Root package exports."""

from talent_screener.composition_root.container import (
    ApplicationContainer,
    ExternalAdapterRegistry,
    Phase3PipelineBundle,
    RepositoryRegistry,
    build_in_memory_repositories,
    build_phase3_container,
    build_phase3_external_adapters,
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
    "RepositoryRegistry",
    "UseCaseCatalogPort",
    "build_in_memory_repositories",
    "build_phase3_container",
    "build_phase3_external_adapters",
]
