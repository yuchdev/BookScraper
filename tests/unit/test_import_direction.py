"""Guard the one-way import direction for ``src/bookscraper/backends/``.

The architecture is layered: ``commands`` may import ``scraping`` and ``backends``; ``scraping``
may import ``backends`` (types) and ``book_utils``; ``backends`` may import only ``book_utils`` and
the package-root pure modules (``models`` / ``isbn``). Nothing under ``backends`` may import
``scraping`` or ``commands`` - that is why the v1-to-v2 transform is *injected* into
``backends/migration.py`` rather than imported. This test fails loudly if any ``backends`` module
reaches back up the dependency graph (both absolute and relative imports are resolved and checked).
"""

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "bookscraper"
BACKENDS_ROOT = SRC_ROOT / "backends"

# Package prefixes a module under backends/ must never import from.
FORBIDDEN_PREFIXES = ("bookscraper.scraping", "bookscraper.commands")


def _module_package(path: Path) -> list[str]:
    """Return the dotted package parts of the package *containing* the module at ``path``.

    :param path: A ``.py`` file under :data:`SRC_ROOT`.
    :returns: The parts of the containing package, e.g. ``["bookscraper", "backends", "mongo"]``
        for ``backends/mongo/database.py``.
    """
    relative = path.relative_to(SRC_ROOT.parent).with_suffix("")
    parts = list(relative.parts)
    return parts[:-1]


def _imported_modules(path: Path) -> list[str]:
    """Return every fully-resolved module name imported by the file at ``path``.

    Absolute imports are taken verbatim; relative imports are resolved against the module's
    containing package so ``from ..scraping import x`` becomes ``bookscraper.scraping``.

    :param path: The module file to scan.
    :returns: The resolved dotted module names it imports.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_package(path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    modules.append(node.module)
                continue
            base = package[: len(package) - (node.level - 1)]
            resolved = base + ([node.module] if node.module else [])
            modules.append(".".join(resolved))
    return modules


def test_backends_never_imports_scraping_or_commands():
    """
    [Unit] import direction: verifies no backends module imports bookscraper.scraping or .commands.

    Scenario:
        Given every .py file under src/bookscraper/backends
        When each file's imports are parsed and resolved (absolute and relative)
        Then none resolves to a bookscraper.scraping or bookscraper.commands module.

    Boundaries:
        - Static AST scan; both ``import x`` and ``from ..x import y`` forms are resolved
        - The migration transform is injected, so migration.py must stay clean too

    On failure, first check:
        - The reported file; inject the dependency as a parameter instead of importing it
        - Whether a helper belongs at the package root (models/isbn/book_utils) rather than scraping
    """
    offenders = []
    for path in sorted(BACKENDS_ROOT.rglob("*.py")):
        for module in _imported_modules(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                offenders.append(f"{path.relative_to(SRC_ROOT).as_posix()} -> {module}")
    assert offenders == [], f"backends must not import scraping/commands: {offenders}"
