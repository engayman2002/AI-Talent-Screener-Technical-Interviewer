"""Generic Inbound Driving Port protocols for Commands and Queries."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class UseCasePort[TRequest, TResponse](Protocol):
    """Generic contract for single-responsibility Application command interactors."""

    async def execute(self, request: TRequest) -> TResponse: ...


@runtime_checkable
class QueryUseCasePort[TQuery, TResult](Protocol):
    """Generic contract for read-only Application query interactors."""

    async def execute(self, query: TQuery) -> TResult: ...
