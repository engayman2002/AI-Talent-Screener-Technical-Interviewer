"""Architectural fitness tests verifying Clean Architecture and zero-framework domain purity."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "talent_screener"
DOMAIN_ROOT = SRC_ROOT / "domain"
APPLICATION_ROOT = SRC_ROOT / "application"
CONTEXTS_ROOT = DOMAIN_ROOT / "contexts"

FORBIDDEN_THIRD_PARTY_PACKAGES = frozenset(
    {
        "pydantic",
        "fastapi",
        "sqlalchemy",
        "langchain",
        "langchain_core",
        "langchain_community",
        "langgraph",
        "celery",
        "redis",
        "qdrant_client",
        "httpx",
        "aiohttp",
    }
)


def _resolve_module_name(file_path: Path, node: ast.ImportFrom) -> str:
    """Resolve absolute dotted module name for an ast.ImportFrom node."""
    if node.level == 0:
        return node.module or ""
    rel_parts = file_path.relative_to(SRC_ROOT.parent).with_suffix("").parts
    if file_path.name == "__init__.py":
        package_parts = list(rel_parts[:-1])
    else:
        package_parts = list(rel_parts[:-1])
    up_levels = node.level - 1
    if up_levels > len(package_parts):
        raise AssertionError(f"Relative import escapes source root in {file_path}")
    base_parts = package_parts[: len(package_parts) - up_levels]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _extract_imported_modules(file_path: Path) -> list[str]:
    """Parse AST of a Python file and collect all resolved imported module names."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_module_name(file_path, node)
            if resolved:
                imported.append(resolved)
    return imported


@pytest.mark.architecture
def test_domain_layer_uses_exclusively_python_stdlib_and_domain_modules() -> None:
    """Every file under domain/ must import only stdlib modules or talent_screener.domain."""
    domain_files = sorted(DOMAIN_ROOT.rglob("*.py"))
    assert domain_files, "Expected Python modules under domain/."

    violations: list[str] = []
    for py_file in domain_files:
        for mod_name in _extract_imported_modules(py_file):
            top_level = mod_name.split(".")[0]
            if mod_name.startswith("talent_screener.application"):
                violations.append(f"{py_file}: forbidden inward violation -> {mod_name}")
            elif mod_name.startswith("talent_screener.infrastructure"):
                violations.append(f"{py_file}: forbidden infrastructure import -> {mod_name}")
            elif mod_name.startswith("talent_screener.presentation"):
                violations.append(f"{py_file}: forbidden presentation import -> {mod_name}")
            elif top_level in FORBIDDEN_THIRD_PARTY_PACKAGES:
                violations.append(f"{py_file}: forbidden third-party import -> {mod_name}")
            elif mod_name == "talent_screener" or mod_name.startswith("talent_screener.domain"):
                continue
            elif top_level not in sys.stdlib_module_names:
                violations.append(f"{py_file}: non-stdlib import detected -> {mod_name}")

    assert not violations, "Domain dependency rule violations:\n" + "\n".join(violations)


@pytest.mark.architecture
def test_application_layer_respects_inward_dependency_rule() -> None:
    """Application ports/DTOs may only import stdlib, domain, or application modules."""
    app_files = sorted(APPLICATION_ROOT.rglob("*.py"))
    assert app_files, "Expected Python modules under application/."

    violations: list[str] = []
    for py_file in app_files:
        for mod_name in _extract_imported_modules(py_file):
            top_level = mod_name.split(".")[0]
            if mod_name.startswith("talent_screener.infrastructure"):
                violations.append(f"{py_file}: forbidden infrastructure import -> {mod_name}")
            elif mod_name.startswith("talent_screener.presentation"):
                violations.append(f"{py_file}: forbidden presentation import -> {mod_name}")
            elif top_level in FORBIDDEN_THIRD_PARTY_PACKAGES:
                violations.append(f"{py_file}: forbidden third-party import -> {mod_name}")
            elif mod_name.startswith("talent_screener.domain") or mod_name.startswith(
                "talent_screener.application"
            ):
                continue
            elif top_level not in sys.stdlib_module_names:
                violations.append(f"{py_file}: non-stdlib import detected -> {mod_name}")

    assert not violations, "Application dependency rule violations:\n" + "\n".join(violations)


@pytest.mark.architecture
def test_bounded_contexts_have_zero_cross_context_imports() -> None:
    """Each of the 5 Bounded Contexts must not directly import another context."""
    context_dirs = [p for p in CONTEXTS_ROOT.iterdir() if p.is_dir() and p.name != "__pycache__"]
    context_names = {p.name for p in context_dirs}
    assert context_names == {
        "recruitment",
        "document_intelligence",
        "live_interview",
        "evaluation_analytics",
        "identity_governance",
    }

    violations: list[str] = []
    for ctx_dir in context_dirs:
        own_prefix = f"talent_screener.domain.contexts.{ctx_dir.name}"
        for py_file in sorted(ctx_dir.rglob("*.py")):
            for mod_name in _extract_imported_modules(py_file):
                if mod_name.startswith("talent_screener.domain.contexts.") and not (
                    mod_name == own_prefix or mod_name.startswith(f"{own_prefix}.")
                ):
                    violations.append(f"{py_file}: cross-context import -> {mod_name}")

    assert not violations, "Cross-context coupling detected:\n" + "\n".join(violations)
