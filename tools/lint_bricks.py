#!/usr/bin/env python3
"""Check the enforceable brick rules using only the Python standard library."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import policy as eval_policy  # type: ignore[no-redef]
else:
    from . import policy as eval_policy


REQUIRED = (
    "__init__.py",
    "contract.py",
    "input/AGENTS.md",
    "input/adapters",
    "input/config.yml",
    "runner/AGENTS.md",
    "runner/__init__.py",
    "runner/run.py",
    "src/AGENTS.md",
    "evaluation/AGENTS.md",
    "evaluation/policy.yml",
    "evaluation/evaluate.py",
    "evaluation/data.lock",
)
# Removed with saved mode: the linter rejects these rather than ignoring them.
RETIRED = {
    "input/evidence.py": "saved mode is gone; adapters call ctx['io']",
    "input/data": "saved examples are gone; the runner stores evaluation data",
    "runner/runs": "run records are gone; the runtime captures runs",
    "runner/rng.py": "the runtime creates run IDs",
    "runner/tests": "smoke tests are gone; evaluation lives in evaluation/",
    "src/tests": "unit tests are gone; check invariants as properties in evaluation/",
}
DIRECT_IO = {
    "boto3", "ftplib", "http", "httpx", "importlib", "io", "os", "pathlib", "psycopg",
    "requests", "shutil", "smtplib", "socket", "sqlite3", "ssl", "subprocess", "urllib",
    "asyncio", "aiohttp", "websockets", "ctypes",
}
EVALUATE_FUNCTIONS = ("build", "score", "generate")
GATE_OPERATORS = {">=", "<=", ">", "<"}
FRAMEWORK_MODULES = {"_runtime"}


@dataclass
class Contract:
    version: int = 0
    dependencies: dict[str, str] = field(default_factory=dict)
    owned_state: tuple[str, ...] = ()
    lane: str = "strict"
    deterministic: bool = False


class Lint:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def add(self, path: Path, message: str, *, warning: bool = False) -> None:
        try:
            name = path.relative_to(self.root)
        except ValueError:
            name = path
        target = self.warnings if warning else self.errors
        target.append(f"{name}: {message}")

    def tree(self, path: Path) -> ast.Module | None:
        if not path.is_file():
            return None
        try:
            return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            self.add(path, f"cannot parse Python: {exc}")
            return None


def imported_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0 and node.module == "bricks":
        # `from bricks import _runtime` names a module, `from bricks import x` a brick.
        framework = [f"bricks.{alias.name}" for alias in node.names if alias.name.startswith("_")]
        if framework:
            return framework
    return ["." * node.level + (node.module or "")]


def called_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def annotation_name(node: ast.expr | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def assigned_literal(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                value = node.value
        if value is not None:
            try:
                return ast.literal_eval(value)
            except (TypeError, ValueError):
                return None
    return None


def lint_contract(brick: Path, lint: Lint) -> Contract:
    path = brick / "contract.py"
    tree = lint.tree(path)
    if not tree:
        return Contract()

    version = assigned_literal(tree, "CONTRACT_VERSION")
    dependencies = assigned_literal(tree, "SIBLING_DEPENDENCIES")
    owned_state = assigned_literal(tree, "OWNED_STATE")
    lane = assigned_literal(tree, "LANE")
    deterministic = assigned_literal(tree, "DETERMINISTIC")
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        lint.add(path, "CONTRACT_VERSION must be a positive integer")
        version = 0
    if not isinstance(dependencies, dict) or not all(
        isinstance(name, str)
        and isinstance(mode, str)
        and mode in {"eventual", "orchestrated"}
        for name, mode in (dependencies.items() if isinstance(dependencies, dict) else ())
    ):
        lint.add(path, "SIBLING_DEPENDENCIES must map names to eventual or orchestrated")
        dependencies = {}
    if not isinstance(owned_state, (tuple, list)) or not all(
        isinstance(item, str) and item for item in owned_state
    ):
        lint.add(path, "OWNED_STATE must contain non-empty resource identifiers")
        owned_state = ()
    if not isinstance(deterministic, bool):
        lint.add(path, "DETERMINISTIC must be True or False")
        deterministic = False
    for class_name in ("BrickInput", "BrickOutput"):
        declaration = classes.get(class_name)
        if not declaration or not any(annotation_name(base) == "TypedDict" for base in declaration.bases):
            lint.add(path, f"{class_name} must be declared as a TypedDict")
    if lane is None:
        lane = "strict"
    elif lane not in LANES:
        lint.add(path, f"LANE must be one of {', '.join(sorted(LANES))}")
        lane = "strict"

    return Contract(version, dict(dependencies), tuple(owned_state), lane, deterministic)


def lint_python(brick: Path, contract: Contract, lint: Lint) -> None:
    entry = lint.tree(brick / "__init__.py")
    if entry:
        imports_run = any(
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "runner.run"
            and [name.name for name in node.names] == ["run"]
            for node in entry.body
        )
        exports = None
        for node in entry.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            ):
                try:
                    exports = ast.literal_eval(node.value)
                except (TypeError, ValueError):
                    pass
        if not imports_run or exports != ["run"]:
            lint.add(brick / "__init__.py", "must import and export only .runner.run")

    runner = lint.tree(brick / "runner/run.py")
    if runner:
        function = next(
            (node for node in runner.body if isinstance(node, ast.FunctionDef) and node.name == "run"),
            None,
        )
        if function is None:
            lint.add(brick / "runner/run.py", "must define run")
        else:
            args = function.args
            if args.kwonlyargs or args.vararg or args.kwarg or len(args.args) != 1:
                lint.add(brick / "runner/run.py", "run takes exactly one argument, the brick input, and no mode options")
            input_type = annotation_name(args.args[0].annotation) if args.args else ""
            if input_type != "BrickInput" or annotation_name(function.returns) != "BrickOutput":
                lint.add(brick / "runner/run.py", "run must use BrickInput and BrickOutput annotations")

    actual_dependencies: set[str] = set()
    for path in brick.rglob("*.py"):
        tree = lint.tree(path)
        if not tree:
            continue
        relative = path.relative_to(brick).parts
        role = relative[0] if len(relative) > 1 else "entry"
        adapter = relative[:2] == ("input", "adapters")
        sibling_runs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    package_depth = len(relative[:-1])
                    if node.level > package_depth + 1:
                        lint.add(path, "relative import may not escape the brick")
                for module in imported_modules(node):
                    parts = module.lstrip(".").split(".")
                    if role == "runner" and "input" in parts:
                        lint.add(path, "runner may not import input")
                    if role != "evaluation" and "evaluation" in parts:
                        lint.add(path, "only evaluation/ may import evaluation/")
                    if adapter and ({"src", "runner"} & set(parts)):
                        lint.add(path, "adapter may not import src or runner")
                    if role in {"src", "evaluation"}:
                        if "runner" in parts:
                            lint.add(path, f"{role} may not import runner")
                        if parts[0] in DIRECT_IO:
                            lint.add(path, f"{role} imports direct-I/O module {parts[0]!r}")
                    if role == "evaluation":
                        lint_evaluation_import(path, node, module, parts, brick, lint)
                    if len(parts) > 1 and parts[0] == "bricks" and parts[1].startswith("_"):
                        if parts[1] not in FRAMEWORK_MODULES:
                            lint.add(path, f"bricks.{parts[1]} is not a framework module bricks may use")
                        elif role != "runner":
                            lint.add(path, "only runner/ may import the framework runtime")
                        continue
                    if (
                        isinstance(node, ast.ImportFrom)
                        and parts == ["bricks"]
                        and any(not alias.name.startswith("_") for alias in node.names)
                    ):
                        lint.add(path, "import a sibling's run as from bricks.<name> import run")
                        continue
                    if len(parts) > 1 and parts[0] == "bricks" and parts[1] != brick.name:
                        actual_dependencies.add(parts[1])
                        if not adapter:
                            lint.add(path, "only an input adapter may import a sibling brick")
                        elif (
                            not isinstance(node, ast.ImportFrom)
                            or module != f"bricks.{parts[1]}"
                            or [n.name for n in node.names] != ["run"]
                        ):
                            lint.add(path, "sibling adapter may import only run")
                        else:
                            sibling_runs.update(alias.asname or alias.name for alias in node.names)
                        if parts[1] not in contract.dependencies:
                            lint.add(path, f"sibling dependency {parts[1]!r} is not declared")
            if role in {"src", "evaluation"} and isinstance(node, ast.Call) and called_name(node) in {"open", "__import__"}:
                lint.add(path, f"{role} may not call {called_name(node)}(); do I/O through an adapter")
        if adapter:
            lint_adapter(path, tree, sibling_runs, lint)

    for dependency in contract.dependencies.keys() - actual_dependencies:
        lint.add(brick / "contract.py", f"declared dependency {dependency!r} has no static adapter import", warning=True)


def lint_evaluation_import(
    path: Path, node: ast.Import | ast.ImportFrom, module: str, parts: list[str], brick: Path, lint: Lint
) -> None:
    """evaluation/ sees the brick only through its contract types."""
    if isinstance(node, ast.ImportFrom) and node.level:
        if node.level == 1:
            return  # its own folder
        if node.level == 2 and node.module == "contract":
            return
        lint.add(path, "evaluation/ may import only this brick's contract, never run or its parts")
    elif parts[0] == "bricks":
        if module != f"bricks.{brick.name}.contract":
            lint.add(path, "evaluation/ may import only this brick's contract, never run or its parts")


def lint_adapter(path: Path, tree: ast.Module, sibling_runs: set[str], lint: Lint) -> None:
    """Every adapter call goes through ctx["io"]; a sibling's run is passed, never called."""
    routed = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = called_name(node)
        if isinstance(node.func, ast.Attribute) and name in {"external", "sibling"}:
            routed = True
        if isinstance(node.func, ast.Name) and node.func.id in sibling_runs:
            lint.add(path, "call a sibling through ctx['io'].sibling(name, request, run), never directly")
    if not routed:
        lint.add(path, "adapter makes no call through ctx['io'].external or ctx['io'].sibling", warning=True)


def lint_evaluation(brick: Path, lint: Lint) -> dict[str, Any] | None:
    folder = brick / "evaluation"
    tree = lint.tree(folder / "evaluate.py")
    if tree:
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        for function in EVALUATE_FUNCTIONS:
            if function not in defined:
                lint.add(folder / "evaluate.py", f"must define {function}()")
        gates = assigned_literal(tree, "GATES")
        if not isinstance(gates, (tuple, list)) or not all(
            isinstance(gate, (tuple, list)) and len(gate) == 3
            and isinstance(gate[0], str) and gate[1] in GATE_OPERATORS
            and isinstance(gate[2], (int, float)) and not isinstance(gate[2], bool)
            for gate in gates
        ):
            lint.add(folder / "evaluate.py", "GATES must be a literal tuple of (term, operator, threshold)")

    lock = folder / "data.lock"
    if lock.is_file():
        text = lock.read_text(encoding="utf-8").strip()
        valid = text == "unpinned" or (
            text.startswith("sha256:") and len(text) == 71
            and all(c in "0123456789abcdef" for c in text[7:])
        )
        if not valid:
            lint.add(lock, "data.lock must hold 'unpinned' or a sha256:<64 hex> dataset hash")

    path = folder / "policy.yml"
    if not path.is_file():
        return None
    try:
        policy = eval_policy.load(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        lint.add(path, f"cannot parse policy: {exc}")
        return None
    problems = eval_policy.check(policy)
    for problem in problems:
        lint.add(path, problem)
    return None if problems else policy


def lint_retired(brick: Path, lint: Lint) -> None:
    for relative, reason in RETIRED.items():
        if (brick / relative).exists():
            lint.add(brick / relative, f"retired path: {reason}")


def lint_strict(brick: Path, contract: Contract, lint: Lint) -> None:
    """The regular lane. Every rule above already applies; this adds none."""


PURE_FORBIDDEN = DIRECT_IO | {"random", "time"}
CLOCK_CALLS = {"now", "today", "utcnow"}


def lint_pure(brick: Path, contract: Contract, lint: Lint) -> None:
    """A pure brick's output is a function of its input and nothing else.

    Adapters are the only sanctioned channel to the outside, so a pure brick
    has none, and therefore no sibling dependencies either. The direct-I/O
    ban every ``src/`` already carries widens to the whole brick, joined by
    ``random`` and ``time``, and by clock calls. ``runner/`` is exempt because
    it hands the run to the framework runtime; ``evaluation/`` is exempt because
    it is not the brick: it is how the brick is judged, and it may seed its own
    generators. ``datetime`` stays importable: arithmetic on a caller-supplied
    time is pure; reading ``now()`` is not.
    """
    adapters = sorted((brick / "input/adapters").glob("*.py"))
    for path in adapters:
        lint.add(path, "pure brick may not have adapters")
    if contract.dependencies:
        lint.add(brick / "contract.py", "pure brick may not declare sibling dependencies")
    for path in brick.rglob("*.py"):
        relative = path.relative_to(brick).parts
        if relative[0] in {"runner", "evaluation"}:
            continue
        tree = lint.tree(path)
        if not tree:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in imported_modules(node):
                    name = module.lstrip(".").split(".")[0]
                    # src/ already reports DIRECT_IO under the rule every brick gets.
                    if name in PURE_FORBIDDEN and not (relative[0] == "src" and name in DIRECT_IO):
                        lint.add(path, f"pure brick imports {name!r} outside runner/")
            if isinstance(node, ast.Call) and called_name(node) in CLOCK_CALLS:
                lint.add(path, f"pure brick reads the clock with {called_name(node)}()")


# A lane is a declared enforcement class: the rules above plus the ones its
# enforcer adds. A lane may only add rules, never relax one (AGENTS.md:
# "may not relax a parent rule"), and it may not exist without an enforcer,
# which is why the table maps each name to a function rather than listing
# names. A contract with no LANE is in the strict lane.
def lint_workflow(brick: Path, contract: Contract, lint: Lint) -> None:
    """A workflow is invoked as a whole operation, never imported for its parts.

    Two consequences, one enforced here and one in the graph. Here: it is the
    only kind of brick that may carry a process door, ``__main__.py``, and
    that door may import nothing from the brick tree except the brick's own
    ``run`` -- the same rule a sibling adapter lives under, applied to the
    outside world. In the graph: no contract may list a workflow as a sibling
    dependency.
    """
    door = lint.tree(brick / "__main__.py")
    if not door:
        return
    own_run = {".", ".runner.run", f"bricks.{brick.name}"}
    for node in ast.walk(door):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for module in imported_modules(node):
            inside = module.startswith(".") or module.split(".")[0] == "bricks"
            if not inside:
                if module.split(".")[0] in DIRECT_IO - {"os"}:
                    lint.add(brick / "__main__.py", f"__main__ imports direct-I/O module {module.split('.')[0]!r}; route I/O through an adapter")
                continue
            names = [item.name for item in node.names]
            if not (isinstance(node, ast.ImportFrom) and module in own_run and names == ["run"]):
                lint.add(brick / "__main__.py", "__main__ may import only this brick's run")


LANES = {
    "strict": lint_strict,
    "pure": lint_pure,
    "workflow": lint_workflow,
}


def lint_brick(brick: Path, contract: Contract, lint: Lint) -> None:
    for relative in REQUIRED:
        if not (brick / relative).exists():
            lint.add(brick / relative, "required brick path is missing")
    lint_retired(brick, lint)
    lint_python(brick, contract, lint)
    lint_evaluation(brick, lint)
    if contract.lane != "workflow" and (brick / "__main__.py").exists():
        lint.add(brick / "__main__.py", "only a workflow brick may have __main__.py")
    LANES[contract.lane](brick, contract, lint)


def lint_graph(
    bricks: list[Path], contracts: dict[str, Contract], lint: Lint
) -> None:
    names = set(contracts)
    owners: dict[str, str] = {}
    graph: dict[str, set[str]] = {name: set() for name in names}

    def where(name: str) -> Path:
        return next(brick for brick in bricks if brick.name == name) / "contract.py"

    for name, contract in contracts.items():
        for dependency in contract.dependencies:
            if dependency == name:
                lint.add(where(name), "brick may not depend on itself")
            elif dependency not in names:
                lint.add(where(name), f"unknown sibling dependency {dependency!r}")
            else:
                if contracts[dependency].lane == "workflow":
                    lint.add(
                        where(name),
                        f"sibling dependency {dependency!r} is a workflow brick; a workflow is invoked as a whole, not depended on",
                    )
                graph[name].add(dependency)
        for resource in contract.owned_state:
            if resource in owners:
                lint.add(where(name), f"state {resource!r} is already owned by {owners[resource]!r}")
            else:
                owners[resource] = name

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in visiting:
            cycle = " -> ".join((*trail, name))
            lint.add(lint.root / "bricks", f"sibling dependency cycle: {cycle}")
            return
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency, (*trail, name))
        visiting.remove(name)
        visited.add(name)

    for name in sorted(names):
        visit(name, ())


def lint_repo(root: Path) -> Lint:
    """Lint every brick under ``root`` and return the findings without printing.

    ``main`` is the command; this is the function a test calls on a fixture tree.
    """
    lint = Lint(root)
    bricks = root / "bricks"
    for path in (root / "AGENTS.md", bricks / "AGENTS.md", bricks / "_runtime.py", bricks / "_yaml.py"):
        if not path.is_file():
            lint.add(path, "required framework file is missing")
    for path in root.rglob("BRICK.md"):
        lint.add(path, "contract must be named AGENTS.md")
    if bricks.is_dir():
        brick_paths = sorted(
            path
            for path in bricks.iterdir()
            if path.is_dir() and not path.name.startswith((".", "_"))
        )
        contracts = {brick.name: lint_contract(brick, lint) for brick in brick_paths}
        lint_graph(brick_paths, contracts, lint)
        for brick in brick_paths:
            lint_brick(brick, contracts[brick.name], lint)
    else:
        lint.add(bricks, "bricks directory is missing")
    return lint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    lint = lint_repo(parser.parse_args().root.resolve())
    for item in lint.warnings:
        print(f"WARNING {item}")
    for item in lint.errors:
        print(f"ERROR {item}")
    print(f"brick lint {'failed' if lint.errors else 'passed'}: {len(lint.errors)} errors, {len(lint.warnings)} warnings")
    return bool(lint.errors)


if __name__ == "__main__":
    sys.exit(main())
