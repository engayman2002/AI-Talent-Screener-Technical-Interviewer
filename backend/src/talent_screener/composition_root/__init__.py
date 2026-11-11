"""Composition Root package exports."""

from talent_screener.composition_root.container import (
    ApplicationContainer,
    ExternalAdapterRegistry,
    RepositoryRegistry,
    build_in_memory_repositories,
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
    "RepositoryRegistry",
    "UseCaseCatalogPort",
    "build_in_memory_repositories",
]
