"""AST-based chunk extraction for Python source files.

Uses Python's built-in `ast` module for reliable symbol boundary detection.
Produces the same Chunk structure as the regex extractor but with:
- Accurate end_lineno (from ast node.end_lineno, Python 3.8+)
- Correct decorator start line (decorator included in chunk boundary)
- Better nested structure handling (class methods extracted individually)
- Docstring detection (chunk_type preserves original; docstring presence recorded)

Falls back to an empty list on SyntaxError or any parse failure so the
caller can fall back to regex extraction.
"""

from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ASTChunkSpec:
    """Lightweight boundary spec produced by AST traversal."""
    chunk_type: str      # class | function | method
    symbol_name: str
    start_line: int      # 1-indexed, includes decorators
    end_line: int        # 1-indexed, inclusive
    is_method: bool = False
    has_docstring: bool = False


def extract_python_ast(source: str, file_path: str) -> list[ASTChunkSpec]:
    """Parse Python source and return chunk specs for all top-level and class-level symbols.

    Returns empty list on parse error — caller should fall back to regex extraction.
    Top-level functions and classes are always included.
    Methods within classes are included as individual chunks (chunk_type='method').
    """
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        logger.debug("AST parse failed for %s: %s", file_path, exc)
        return []
    except Exception as exc:
        logger.debug("AST parse error for %s: %s", file_path, exc)
        return []

    specs: list[ASTChunkSpec] = []
    lines = source.splitlines()

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            specs.append(_function_spec(node, lines, is_method=False))
        elif isinstance(node, ast.ClassDef):
            specs.append(_class_spec(node, lines))
            # Also extract methods as individual chunks
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    specs.append(_function_spec(child, lines, is_method=True))

    return specs


def _decorator_start(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    """Return the first line of the first decorator, or the node's own lineno."""
    if node.decorator_list:
        return node.decorator_list[0].lineno
    return node.lineno


def _has_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    """Return True if the node body starts with a string constant (docstring)."""
    if not node.body:
        return False
    first = node.body[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def _end_line(node: ast.AST, lines: list[str]) -> int:
    """Return the 1-indexed end line of a node.

    Uses node.end_lineno (Python 3.8+). Falls back to scanning for the
    last non-empty line if the attribute is missing.
    """
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    # Fallback: scan from node.lineno to end of file for last non-blank line
    # This should never happen on Python 3.8+ but is a safety net.
    start = getattr(node, "lineno", 1)
    last = start
    for i in range(start, len(lines)):
        if lines[i].strip():
            last = i + 1  # 1-indexed
    return last


def _function_spec(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    is_method: bool,
) -> ASTChunkSpec:
    return ASTChunkSpec(
        chunk_type="method" if is_method else "function",
        symbol_name=node.name,
        start_line=_decorator_start(node),
        end_line=_end_line(node, lines),
        is_method=is_method,
        has_docstring=_has_docstring(node),
    )


def _class_spec(node: ast.ClassDef, lines: list[str]) -> ASTChunkSpec:
    return ASTChunkSpec(
        chunk_type="class",
        symbol_name=node.name,
        start_line=_decorator_start(node),
        end_line=_end_line(node, lines),
        is_method=False,
        has_docstring=_has_docstring(node),
    )
