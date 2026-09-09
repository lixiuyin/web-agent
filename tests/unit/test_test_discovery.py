"""Guard against test definitions that pytest silently cannot collect."""

import ast
from pathlib import Path


def test_no_test_function_is_nested_inside_another_function() -> None:
    hidden: list[str] = []
    for path in Path(__file__).resolve().parents[1].rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            parent = parents.get(node)
            while parent is not None:
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    hidden.append(f"{path.name}:{node.lineno}: {node.name}")
                    break
                parent = parents.get(parent)
    assert not hidden, "pytest cannot collect nested tests:\n" + "\n".join(hidden)
