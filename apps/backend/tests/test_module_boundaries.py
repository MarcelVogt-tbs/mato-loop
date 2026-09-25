"""Executable boundaries for the modular monolith."""
import ast
import subprocess
import sys
from graphlib import TopologicalSorter
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "src/mato_loop_backend"
PREFIX = "mato_loop_backend."
HANDLERS = {
    "tools.files", "tools.web", "tools.media", "tools.sessions", "tools.memory",
    "tools.automations", "tools.skills", "tools.identity", "tools.interaction",
    "tools.computer", "tools.coding",
}


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (item.name for item in node.names)


def test_routes_and_handlers_are_not_shared_service_dependencies():
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        dependencies = set(imports(path))
        if relative.parts[0] != "api" and relative.name != "app.py":
            assert not any(name.startswith(PREFIX + "api.routes") for name in dependencies), relative
        if relative.parts[0] == "integrations":
            assert not dependencies & {PREFIX + name for name in HANDLERS}, relative
        if relative.parts[0] == "integrations" and (relative.name == "client.py" or relative.name.endswith("_client.py")):
            assert not any(name.startswith((PREFIX + "tools.", PREFIX + "agent.", "fastapi")) for name in dependencies), relative


@pytest.mark.parametrize("module", ["tools.contracts", "web.fetch", "files.service", "integrations.bookstack.client"])
def test_leaf_import_does_not_initialize_runtime_or_catalog(module):
    script = (
        f"import importlib, sys; importlib.import_module('{PREFIX}{module}'); "
        f"assert '{PREFIX}agent.runtime' not in sys.modules; "
        f"assert '{PREFIX}tools.catalog' not in sys.modules; "
        f"assert '{PREFIX}integrations.registry' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=30)


def test_routes_do_not_import_other_routes():
    for path in (ROOT / "api/routes").glob("*.py"):
        assert not any(name.startswith(PREFIX + "api.routes.") for name in imports(path)), path.name


def test_integration_adapters_live_in_product_packages():
    integrations = ROOT / "integrations"
    assert {path.name for path in integrations.glob("*.py")} <= {
        "__init__.py", "contracts.py", "execution.py", "registry.py",
        "service.py", "search.py",
    }
    registered_modules = set(imports(integrations / "registry.py"))
    for adapter in integrations.glob("*/adapter.py"):
        assert (adapter.parent / "__init__.py").is_file()
        assert f"{PREFIX}integrations.{adapter.parent.name}.adapter" in registered_modules
    assert not any(
        isinstance(node, ast.ClassDef) and node.name.endswith("IntegrationService")
        for node in ast.parse((integrations / "service.py").read_text()).body
    )


def test_eager_backend_import_graph_is_acyclic():
    modules = {
        PREFIX + str(path.relative_to(ROOT).with_suffix("")).replace("/", ".").removesuffix(".__init__"): path
        for path in ROOT.rglob("*.py")
    }
    graph = {}
    for name, path in modules.items():
        dependencies = set()
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                if node.level:
                    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
                    module = ".".join(package.split(".")[:len(package.split(".")) - node.level + 1]) + "." + module
                dependencies.add(module)
                dependencies.update(module + "." + alias.name for alias in node.names)
        graph[name] = (dependencies & modules.keys()) - {name}
    tuple(TopologicalSorter(graph).static_order())
