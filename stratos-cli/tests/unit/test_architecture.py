"""Architecture rules (Clean Architecture, dependency inversion, no god objects, no global state).

These read the source with `ast`, so they fail the build the moment a rule is broken.
"""

import ast
from collections import defaultdict
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = SRC / "stratos"

# layer -> layers it may import from (besides itself)
ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "utils": {"domain"},
    "config": {"domain", "utils"},
    "logging": {"domain", "utils"},
    "infrastructure": {"domain", "utils", "config", "logging"},
    "application": {"domain", "utils", "config", "logging"},
    "cli": {"domain", "utils", "config", "logging", "application", "infrastructure"},
}
MAX_FUNCTION_LINES = 80
MAX_CLASS_LINES = 500


def modules() -> dict[str, ast.Module]:
    found: dict[str, ast.Module] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = ast.parse(path.read_text(encoding="utf-8"))
    return found


def imported(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return {n for n in names if n == "stratos" or n.startswith("stratos.")}


def layer_of(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else "root"


MODULES = modules()


def test_layers_only_depend_inwards() -> None:
    violations: list[str] = []
    for module, tree in MODULES.items():
        layer = layer_of(module)
        if layer not in ALLOWED:
            continue  # the package root and __main__
        for target in imported(tree):
            target_layer = layer_of(target)
            if target_layer in (layer, "root"):
                continue
            if target_layer not in ALLOWED[layer]:
                violations.append(f"{module} imports {target}")
    assert not violations, "layer violations:\n" + "\n".join(violations)


def test_application_layer_never_imports_concrete_adapters() -> None:
    """Dependency inversion: services depend on ports in `domain.interfaces`, not adapters."""
    offenders = [
        f"{m} -> {t}"
        for m, tree in MODULES.items()
        if layer_of(m) == "application"
        for t in imported(tree)
        if layer_of(t) in {"infrastructure", "cli"}
    ]
    assert offenders == []


def test_only_the_composition_root_wires_adapters() -> None:
    """Commands and rendering never import infrastructure; `cli.context` and `cli.wiring` do."""
    allowed_prefixes = ("stratos.cli.context", "stratos.cli.wiring")
    offenders = [
        f"{m} -> {t}"
        for m, tree in MODULES.items()
        if layer_of(m) == "cli" and not m.startswith(allowed_prefixes)
        for t in imported(tree)
        if layer_of(t) == "infrastructure"
    ]
    assert offenders == []


def test_infrastructure_adapters_do_not_depend_on_each_other_in_cycles() -> None:
    """No import cycles between modules (checked on module-level imports, as Python runs them)."""
    graph: dict[str, set[str]] = defaultdict(set)
    for module, tree in MODULES.items():
        body = [
            n
            for n in tree.body
            if isinstance(n, ast.Import | ast.ImportFrom)
            or (isinstance(n, ast.If) and "TYPE_CHECKING" not in ast.dump(n.test))
        ]
        for node in body:
            for target in imported(node):
                if target in MODULES and target != module:
                    graph[module].add(target)

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for m in MODULES:
        if m not in index:
            visit(m)
    assert cycles == []


def test_no_god_functions_or_god_classes() -> None:
    too_big: list[str] = []
    for module, tree in MODULES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                lines = (node.end_lineno or node.lineno) - node.lineno + 1
                if lines > MAX_FUNCTION_LINES:
                    too_big.append(f"function {module}.{node.name}: {lines} lines")
            elif isinstance(node, ast.ClassDef):
                lines = (node.end_lineno or node.lineno) - node.lineno + 1
                if lines > MAX_CLASS_LINES:
                    too_big.append(f"class {module}.{node.name}: {lines} lines")
    assert too_big == [], (
        f"limits: {MAX_FUNCTION_LINES} lines per function, {MAX_CLASS_LINES} per class"
    )


def test_no_global_mutable_state() -> None:
    """Module-level names are constants (UPPER_CASE), `__all__`, or immutable; no `global`."""
    offenders: list[str] = []
    for module, tree in MODULES.items():
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                mutable = isinstance(
                    node.value,
                    ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp,
                )
                for target in targets:
                    if isinstance(target, ast.Name) and mutable:
                        name = target.id.lstrip("_")
                        if name != "__all__" and target.id != "__all__" and not name.isupper():
                            offenders.append(f"{module}.{target.id}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                offenders.append(f"{module}: global statement")
    assert offenders == []


def test_every_module_has_a_docstring() -> None:
    """PEP 257: every non-empty module explains itself."""
    missing = [
        m
        for m, tree in MODULES.items()
        if tree.body and not ast.get_docstring(tree) and not m.endswith(".__init__")
    ]
    empty_inits_are_fine = [m for m in missing if MODULES[m].body != []]
    assert empty_inits_are_fine == []


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_every_layer_is_covered_by_a_rule(layer: str) -> None:
    assert any(layer_of(m) == layer for m in MODULES), f"no modules found for layer {layer}"
