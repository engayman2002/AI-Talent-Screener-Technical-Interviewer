"""SOLID enforcement and Robert C. Martin Coupling Metrics (Ca, Ce, I, A, D) fitness tests."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import pkgutil
import sys
import typing
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import talent_screener

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "talent_screener"
DOMAIN_ROOT = SRC_ROOT / "domain"
APPLICATION_ROOT = SRC_ROOT / "application"


@dataclass(frozen=True, slots=True)
class PackageCouplingMetrics:
    """Robert C. Martin package coupling metrics computed via AST."""

    package: str
    ca: int
    ce: int
    instability: float
    abstract_classes: int
    total_classes: int
    abstractness: float
    distance_from_main_sequence: float


def _resolve_import_from(file_path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    rel_parts = file_path.relative_to(SRC_ROOT.parent).with_suffix("").parts
    package_parts = list(rel_parts[:-1])
    up_levels = node.level - 1
    base_parts = package_parts[: len(package_parts) - up_levels]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _file_to_module(file_path: Path) -> str:
    rel = file_path.relative_to(SRC_ROOT.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _logical_package(module_name: str) -> str:
    """Map a dotted module path to its architectural package unit."""
    parts = module_name.split(".")
    if len(parts) < 2 or parts[0] != "talent_screener":
        return module_name
    layer = parts[1]
    if layer == "domain":
        return "talent_screener.domain"
    if layer == "application":
        if len(parts) >= 4 and parts[2] == "common" and parts[3] == "ports":
            return "talent_screener.application.common.ports"
        if len(parts) >= 4 and parts[3] in {"ports", "use_cases", "dtos", "event_handlers"}:
            return f"talent_screener.application.{parts[2]}.{parts[3]}"
        if len(parts) >= 3:
            return f"talent_screener.application.{parts[2]}"
        return "talent_screener.application"
    if layer == "infrastructure":
        return (
            f"talent_screener.infrastructure.{parts[2]}"
            if len(parts) >= 3
            else "talent_screener.infrastructure"
        )
    if layer == "presentation":
        return "talent_screener.presentation"
    if layer == "composition_root":
        return "talent_screener.composition_root"
    return module_name


def _is_abstract_class_node(node: ast.ClassDef) -> bool:
    for base in node.bases:
        base_name = ast.unparse(base)
        if base_name in {"Protocol", "ABC"} or base_name.startswith("Protocol["):
            return True
    for stmt in node.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            for dec in stmt.decorator_list:
                if "abstractmethod" in ast.unparse(dec):
                    return True
    return False


def _compute_all_package_metrics() -> dict[str, PackageCouplingMetrics]:
    """Compute exact (Ca, Ce, I, A, D) across all logical packages under src/talent_screener."""
    py_files = sorted(SRC_ROOT.rglob("*.py"))
    pkg_classes: dict[str, tuple[int, int]] = {}  # pkg -> (abstract_count, total_count)
    outgoing_module_edges: dict[str, set[tuple[str, str]]] = {}  # pkg -> {(src_mod, dst_mod)}
    incoming_module_edges: dict[str, set[tuple[str, str]]] = {}  # pkg -> {(src_mod, dst_mod)}

    all_packages: set[str] = set()
    for py_file in py_files:
        mod_name = _file_to_module(py_file)
        pkg = _logical_package(mod_name)
        all_packages.add(pkg)
        pkg_classes.setdefault(pkg, (0, 0))
        outgoing_module_edges.setdefault(pkg, set())
        incoming_module_edges.setdefault(pkg, set())

    for py_file in py_files:
        src_mod = _file_to_module(py_file)
        src_pkg = _logical_package(src_mod)
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        abs_inc = 0
        tot_inc = 0
        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef):
                tot_inc += 1
                if _is_abstract_class_node(stmt):
                    abs_inc += 1
        prev_abs, prev_tot = pkg_classes[src_pkg]
        pkg_classes[src_pkg] = (prev_abs + abs_inc, prev_tot + tot_inc)

        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_import_from(py_file, node)
                if resolved:
                    targets.append(resolved)

            for target in targets:
                if not target.startswith("talent_screener."):
                    continue
                dst_pkg = _logical_package(target)
                if dst_pkg == src_pkg or dst_pkg not in all_packages:
                    continue
                outgoing_module_edges[src_pkg].add((src_mod, target))
                incoming_module_edges[dst_pkg].add((src_mod, target))

    metrics: dict[str, PackageCouplingMetrics] = {}
    for pkg in sorted(all_packages):
        ca = len({src for src, _ in incoming_module_edges[pkg]})
        ce = len({dst for _, dst in outgoing_module_edges[pkg]})
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0
        n_abs, n_tot = pkg_classes[pkg]
        abstractness = n_abs / n_tot if n_tot > 0 else 0.0
        distance = abs(abstractness + instability - 1.0)
        metrics[pkg] = PackageCouplingMetrics(
            package=pkg,
            ca=ca,
            ce=ce,
            instability=instability,
            abstract_classes=n_abs,
            total_classes=n_tot,
            abstractness=abstractness,
            distance_from_main_sequence=distance,
        )
    return metrics


def _iter_submodules(root_pkg: Any) -> list[Any]:
    loaded: list[Any] = []
    for mod_info in pkgutil.walk_packages(root_pkg.__path__, prefix=f"{root_pkg.__name__}."):
        loaded.append(importlib.import_module(mod_info.name))
    return loaded


def _is_abstract_type_annotation(annotation: Any) -> bool:
    """Verify a type annotation resolves to a Protocol or ABC abstraction."""
    origin = typing.get_origin(annotation)
    target = origin if origin is not None else annotation
    if not isinstance(target, type):
        return False
    return bool(getattr(target, "_is_protocol", False) or issubclass(target, ABC))


@pytest.mark.architecture
def test_domain_and_application_have_zero_outer_or_third_party_imports() -> None:
    """Enforce strict Concentric Dependency Rule across domain/ and application/."""
    violations: list[str] = []
    for root in (DOMAIN_ROOT, APPLICATION_ROOT):
        for py_file in sorted(root.rglob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    resolved = _resolve_import_from(py_file, node)
                    if resolved:
                        names.append(resolved)
                for mod in names:
                    top = mod.split(".")[0]
                    if mod.startswith(
                        (
                            "talent_screener.infrastructure",
                            "talent_screener.presentation",
                            "talent_screener.composition_root",
                        )
                    ):
                        violations.append(f"{py_file}: forbidden outer-layer import {mod}")
                    elif (
                        not mod.startswith("talent_screener.")
                        and top not in sys.stdlib_module_names
                    ):
                        violations.append(f"{py_file}: forbidden external import {mod}")
    assert not violations, "Concentric Dependency Rule violations:\n" + "\n".join(violations)


@pytest.mark.architecture
def test_all_ports_are_pure_protocol_or_abc_abstractions() -> None:
    """Verify ISP/DIP: every class inside ports/ packages is a Protocol or ABC."""
    port_files = sorted(APPLICATION_ROOT.rglob("ports/**/*.py"))
    assert port_files
    non_abstract: list[str] = []
    for py_file in port_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not _is_abstract_class_node(node):
                non_abstract.append(f"{py_file}: concrete class {node.name} inside ports/")
    assert not non_abstract, "Non-abstract types found in ports/:\n" + "\n".join(non_abstract)


def _is_frozen_dataclass(cls: object) -> bool:
    if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
        return False
    params = cls.__dict__.get("__dataclass_params__")
    return bool(params is not None and params.frozen)


@pytest.mark.architecture
def test_all_use_cases_satisfy_srp_dip_and_immutable_dto_boundary() -> None:
    """Verify every Use Case exposes only execute(), injects only Ports, and uses frozen DTOs."""
    app_pkg = importlib.import_module("talent_screener.application")
    use_case_classes: list[type[Any]] = []
    for mod in _iter_submodules(app_pkg):
        if ".use_cases." not in mod.__name__:
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ == mod.__name__ and obj.__name__.endswith("UseCase"):
                use_case_classes.append(obj)

    assert len(use_case_classes) >= 22, f"Expected >= 22 UseCases, found {len(use_case_classes)}"

    srp_violations: list[str] = []
    dip_violations: list[str] = []
    dto_violations: list[str] = []

    for uc_cls in use_case_classes:
        # 1. SRP: Single public method 'execute' (plus __init__)
        public_methods = [
            name
            for name, member in uc_cls.__dict__.items()
            if callable(member) and not name.startswith("_")
        ]
        if public_methods != ["execute"]:
            srp_violations.append(f"{uc_cls.__name__}: public methods={public_methods}")

        # 2. DIP: Every __init__ parameter must be typed against an abstract Protocol/ABC Port
        init_hints = typing.get_type_hints(uc_cls.__init__)
        sig = inspect.signature(uc_cls.__init__)
        for param_name in sig.parameters:
            if param_name == "self":
                continue
            ann = init_hints.get(param_name)
            if ann is None or not _is_abstract_type_annotation(ann):
                dip_violations.append(
                    f"{uc_cls.__name__}.__init__({param_name}: {ann!r}) is not an abstract Port."
                )

        # 3. Anti-Corruption DTO Boundary: execute() takes & returns frozen dataclasses
        exec_hints = typing.get_type_hints(uc_cls.execute)
        exec_sig = inspect.signature(uc_cls.execute)
        for param_name in exec_sig.parameters:
            if param_name == "self":
                continue
            p_type = exec_hints.get(param_name)
            if not _is_frozen_dataclass(p_type):
                dto_violations.append(f"{uc_cls.__name__}.execute arg {param_name}: {p_type!r}")
        ret_type = exec_hints.get("return")
        if not _is_frozen_dataclass(ret_type):
            dto_violations.append(f"{uc_cls.__name__}.execute return: {ret_type!r}")

    assert not srp_violations, "SRP violations:\n" + "\n".join(srp_violations)
    assert not dip_violations, "DIP violations:\n" + "\n".join(dip_violations)
    assert not dto_violations, "DTO boundary violations:\n" + "\n".join(dto_violations)


@pytest.mark.architecture
def test_all_application_dtos_are_immutable_frozen_dataclasses() -> None:
    """Every DTO class in application/**/dtos/ must be a frozen dataclass."""
    app_pkg = importlib.import_module("talent_screener.application")
    dto_classes: list[type[Any]] = []
    for mod in _iter_submodules(app_pkg):
        if ".dtos" not in mod.__name__ and not mod.__name__.endswith(".dtos"):
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ == mod.__name__ and (
                obj.__name__.endswith("DTO") or obj.__name__.endswith("Dto")
            ):
                dto_classes.append(obj)

    assert dto_classes
    mutable = [
        cls.__name__
        for cls in dto_classes
        if not _is_frozen_dataclass(cls)
    ]
    assert not mutable, f"Non-frozen DTOs found: {mutable}"


@pytest.mark.architecture
def test_robert_c_martin_coupling_metrics_and_main_sequence() -> None:
    """Compute (Ca, Ce, I, A, D) and assert Phase 2 architectural coupling invariants."""
    metrics = _compute_all_package_metrics()

    # 1. Domain layer is maximally stable (Ce = 0 => I = 0.0)
    domain_metrics = metrics["talent_screener.domain"]
    assert domain_metrics.ce == 0
    assert domain_metrics.instability == 0.0

    # 2. All port packages have Abstractness A == 1.0 and stay on/near Main Sequence
    port_packages = [
        m for name, m in metrics.items() if name.endswith(".ports") and m.total_classes > 0
    ]
    assert port_packages
    for m in port_packages:
        assert m.abstractness == 1.0, f"{m.package} should be 100% abstract; got A={m.abstractness}"

    # 3. Concrete Use Case & Event Handler packages have A = 0.0 and high instability
    use_case_pkgs = [
        m for name, m in metrics.items() if name.endswith(".use_cases") and m.total_classes > 0
    ]
    assert use_case_pkgs
    for m in use_case_pkgs:
        assert m.abstractness == 0.0, f"{m.package} concrete package A={m.abstractness}"
        assert m.instability >= 0.80, f"{m.package} instability too low: I={m.instability:.3f}"
        assert m.distance_from_main_sequence <= 0.20, (
            f"{m.package} breached Main Sequence bound: D={m.distance_from_main_sequence:.3f}"
        )

    event_handler_pkgs = [
        m for name, m in metrics.items() if name.endswith(".event_handlers") and m.total_classes > 0
    ]
    assert event_handler_pkgs
    for m in event_handler_pkgs:
        assert m.abstractness == 0.0
        assert m.instability >= 0.75
        assert m.distance_from_main_sequence <= 0.25

    # 4. Infrastructure adapters are predominantly concrete (thin driver Protocols <= 0.20)
    # and highly replaceable (I >= 0.65), keeping Phase 3 modules cleanly on the Main Sequence.
    infra_pkgs = [
        m
        for name, m in metrics.items()
        if name.startswith("talent_screener.infrastructure.") and m.total_classes > 0
    ]
    assert infra_pkgs
    for m in infra_pkgs:
        assert m.abstractness <= 0.20, f"{m.package} too abstract: A={m.abstractness:.3f}"
        assert m.instability >= 0.65, f"{m.package} instability too low: I={m.instability:.3f}"
    for core_infra in (
        "talent_screener.infrastructure.ai_adapters",
        "talent_screener.infrastructure.messaging",
        "talent_screener.infrastructure.persistence",
        "talent_screener.infrastructure.security",
    ):
        assert metrics[core_infra].distance_from_main_sequence <= 0.15, (
            f"{core_infra} breached Main Sequence D <= 0.15: "
            f"D={metrics[core_infra].distance_from_main_sequence:.3f}"
        )

    # 5. Composition Root is outermost consumer (Ca = 0 => I = 1.0)
    comp_root = metrics["talent_screener.composition_root"]
    assert comp_root.ca == 0
    assert comp_root.instability == 1.0
    assert talent_screener is not None
