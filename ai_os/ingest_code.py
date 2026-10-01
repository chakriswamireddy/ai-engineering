"""Day 10 §4 — ingest_code: AST symbol extraction (the code ingestion seam).

Days 6–9 built a RAG over *prose*, and Day 6's windowed chunker is the right
tool for prose because meaning lives in sentences. Point the same chunker at a
source file and it does the unforgivable: it cuts a `def` in half, separates a
class from its body, and hands two character-windows to retrieval as if they
were independent paragraphs. The retrieval unit for code is not a window — it
is the **symbol** (a function, a class, a method), and the container is the
**module**, whose directory layout is the domain decomposition.

`ast` is the standard library's own parser; using it (rather than installing
tree-sitter) is the first-principles move — we are not learning what a
`FunctionDef` is from a dependency. The extractor is a `NodeVisitor` and emits
one `Symbol` per definition:

    module#Symbol → {kind, signature, docstring, imports, dependencies,
                     first_line, last_line, body}

Design decisions, each owned:

- **The split test is structural.** The AST gives `(first_line, last_line)` for
  every definition, so a chunk boundary is a position in source, never a
  character window across one. No chunk begins `def` and ends mid-`return`.
- **Dependencies are static and one-directional.** `imports` are correct by
  construction; *called names* are recorded as text and never resolved to
  objects — whole-program call resolution belongs to the Day 12/16 agent.
- **Qualified names defeat the `checkout` collision.** A symbol is stored as
  `saleor/checkout/complete_checkout.py#complete_checkout`, never the bare
  identifier. The `module#Symbol` key is the citation, the filter axis, and the
  dedup key at once.
- **The chunk bound is a floor, not a knife.** The bound is per *chunk*
  (`MAX_CHUNK_CHARS`), never per file: a large module still yields one chunk per
  symbol, and only a definition whose own body overflows is split into windows
  tagged with its name. A file-level gate would window `complete_checkout.py`
  whole and lose the symbol `complete_checkout` — a budget guard that deletes the
  answer is worse than the budget.

`PENDING` seam: the extractor is Python-only because Saleor is Python. A
language-agnostic parser is the upgrade path for a non-Python file, and it
plugs in behind this same `ingest_code` seam — the honest way to leave it.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ai_os.chunk import chunk_text

# A single *chunk* over this many characters is a budget catastrophe, not an
# answer (Day 6's budget-floor instinct, applied to symbol size). The bound is
# deliberately a property of the chunk, not of the file: an earlier file-level
# gate (`>40 symbols or >40 KB → window the whole file`) windowed 128 legitimate
# modules — `checkout/complete_checkout.py` (71 KB), `graphql/checkout/types.py`
# (54 KB), `checkout/tests/test_checkout_complete.py` (97 KB) — and so lost the
# very symbols (`complete_checkout`, `Checkout`) the day exists to cite. A large
# module keeps one chunk per symbol; only a single symbol whose *own* body
# overflows is split into windows tagged with its name.
MAX_CHUNK_CHARS = 40_000
WINDOW_SIZE = 1500

# Node kinds. `mutation` is a Saleor-shaped heuristic (a class whose bases name a
# Mutation); `test` is structural (the module lives under tests/ or is `test_*`).
KIND_FUNCTION = "function"
KIND_ASYNC_FUNCTION = "async_function"
KIND_CLASS = "class"
KIND_METHOD = "method"
KIND_ASYNC_METHOD = "async_method"
KIND_MUTATION = "mutation"
KIND_TEST = "test"
KIND_MODULE = "module"


@dataclass
class Symbol:
    """The atomic retrieval unit: one definition, from signature to last line.

    `doc_id` is `module#Symbol` — the citation handle, the metadata-filter axis
    (`module_prefix` / `kind`), and the dedup key. `body` is the *evidence* (the
    full source slice); `signature` + `docstring` are the *interface* the store
    embeds (see `interface_text`).
    """

    module: str
    symbol: str
    kind: str
    signature: str
    docstring: str
    imports: list[str]
    dependencies: list[str]
    first_line: int
    last_line: int
    body: str

    @property
    def doc_id(self) -> str:
        return f"{self.module}#{self.symbol}"


def is_test_module(module: str) -> bool:
    """Structural test detection: a `tests/` package or a `test_*.py` file."""
    parts = module.split("/")
    return ("tests" in parts[:-1]) or parts[-1].startswith("test_")


def interface_text(sym: Symbol) -> str:
    """What gets *embedded* (retrieval matches this): name + signature +
    docstring — "what the symbol does", in the mixture of identifiers and prose
    the query itself speaks. The `body` rides as plain `text` for the context
    builder (the ~hundreds of lines of cited evidence), so the vector stays
    thin and the match stays honest.

    A `module`/window symbol has no interface of its own, so its body *is* the
    embeddable text (the generated-file fallback must not embed a bare name).
    """
    if sym.kind == KIND_MODULE:
        return sym.body
    parts = [sym.symbol, sym.signature, sym.docstring]
    return "\n".join(p for p in parts if p).strip()


def _module_imports(tree: ast.Module) -> list[str]:
    """Module-level `import` / `from … import …` as dotted strings.

    Static, cheap, correct — the semantic edge of the dependency graph. A
    `from saleor.order.utils import create_order_from_checkout` records
    `saleor.order.utils.create_order_from_checkout`, so the graph points at the
    *order* domain without resolving anything.
    """
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.extend(f"{mod}.{a.name}" if mod else a.name for a in node.names)
    return out


def _called_names(node: ast.AST) -> set[str]:
    """Names textually *called* inside `node` — the outgoing edges.

    `foo()` contributes `foo`; `self.bar()` contributes `bar`. No resolution:
    the map records that a call-shaped name appears, never which definition it
    binds to (Day 12/16 walks the graph; Day 10 just stores it).
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _bases(node: ast.ClassDef) -> list[str]:
    out: list[str] = []
    for base in node.bases:
        try:
            out.append(ast.unparse(base))
        except Exception:  # pragma: no cover — unparse is total in practice
            out.append("")
    return [b for b in out if b]


def _signature(node: ast.AST, name: str, *, is_async: bool = False) -> str:
    """`name(args) -> returns`, via `ast.unparse` — compressed meaning."""
    if isinstance(node, ast.ClassDef):
        bases = _bases(node)
        return f"class {name}({', '.join(bases)})" if bases else f"class {name}"
    prefix = "async def" if is_async else "def"
    args = ast.unparse(node.args)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {name}({args}){ret}"


def _body(source_lines: list[str], node: ast.AST) -> str:
    first = getattr(node, "lineno", 1)
    last = getattr(node, "end_lineno", first)
    return "\n".join(source_lines[first - 1:last])


class SymbolExtractor(ast.NodeVisitor):
    """Walk a module, emitting one `Symbol` per definition.

    Module-level functions/classes are visited; a class's direct methods and
    nested classes are visited; functions are *not* descended into (a nested
    closure is not an independent retrieval unit — the enclosing `def` already
    carries it as its body).
    """

    def __init__(self, module: str, source: str) -> None:
        self.module = module
        self.source = source
        self.lines = source.splitlines()
        self.symbols: list[Symbol] = []
        self.imports: list[str] = []
        self._test = is_test_module(module)

    # ------------------------------------------------------------ visitors

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.imports.extend(a.name for a in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        mod = node.module or ""
        self.imports.extend(f"{mod}.{a.name}" if mod else a.name
                            for a in node.names)

    def visit_ClassDef(self, node: ast.ClassDef, prefix: str = "") -> None:  # noqa: N802
        # Nested classes carry their enclosing scope (`User.Meta`, not bare
        # `Meta`) so `module#Symbol` stays a unique dedup/citation key: Saleor has
        # 15 `class Meta`s in one module, and a bare name collapses them to one.
        qualname = f"{prefix}{node.name}"
        kind = KIND_CLASS
        bases = _bases(node)
        if any("Mutation" in b for b in bases):
            kind = KIND_MUTATION
        if self._test:
            kind = KIND_TEST
        self._emit(node, qualname, kind)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m_kind = (KIND_TEST if self._test
                          else KIND_ASYNC_METHOD if isinstance(child, ast.AsyncFunctionDef)
                          else KIND_METHOD)
                self._emit(child, f"{qualname}.{child.name}", m_kind,
                           is_async=isinstance(child, ast.AsyncFunctionDef))
            elif isinstance(child, ast.ClassDef):
                self.visit_ClassDef(child, prefix=f"{qualname}.")
        # do not generic_visit: function bodies are not descended into

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._emit(node, node.name,
                   KIND_TEST if self._test else KIND_FUNCTION)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._emit(node, node.name,
                   KIND_TEST if self._test else KIND_ASYNC_FUNCTION,
                   is_async=True)

    # ------------------------------------------------------------ emission

    def _emit(self, node: ast.AST, name: str, kind: str,
              *, is_async: bool = False) -> None:
        imports = list(self.imports)
        deps = set(_called_names(node))
        deps.update(imp.rsplit(".", 1)[-1] for imp in imports)
        self.symbols.append(Symbol(
            module=self.module,
            symbol=name,
            kind=kind,
            signature=_signature(node, name, is_async=is_async),
            docstring=ast.get_docstring(node, clean=True) or "",
            imports=imports,
            dependencies=sorted(deps),
            first_line=getattr(node, "lineno", 1),
            last_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
            body=_body(self.lines, node),
        ))


def _module_run(tree: ast.Module, module: str, source: str) -> Symbol | None:
    """Group top-level statements that belong to no definition (constants,
    wiring) into one small chunk, so a module's configuration is retrievable
    without inventing a symbol for it. None when the module is only defs."""
    lines = source.splitlines()
    parts: list[str] = []
    first: int | None = None
    last = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue  # the module docstring is not configuration
        ln = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", ln)
        first = ln if first is None else first
        last = max(last, end)
        parts.extend(lines[ln - 1:end])
    if not parts or first is None:
        return None
    return Symbol(
        module=module, symbol="module", kind=KIND_MODULE,
        signature="", docstring="", imports=[],
        dependencies=[], first_line=first, last_line=last,
        body="\n".join(parts),
    )


def _windowed(source: str, module: str, symbol: str | None = None,
              line_offset: int = 0) -> list[Symbol]:
    """Day-6 windowed fallback for a source text too large to be one chunk.

    Used two ways: for an unparseable file (`symbol=None` → `window<N>` ids,
    never a crash), and for a *single* symbol whose own body overflows
    `MAX_CHUNK_CHARS` (`symbol="WebhookPlugin"` → `WebhookPlugin#window<N>` ids,
    so the citation still points at the enclosing symbol). Windows are
    `kind="module"` — the split test is *known* to be structural-only for them.
    """
    out: list[Symbol] = []
    offset = 0
    name = f"{symbol}#" if symbol else ""
    for i, window in enumerate(chunk_text(source, size=WINDOW_SIZE, overlap=0)):
        line = line_offset + source.count("\n", 0, offset) + 1
        out.append(Symbol(
            module=module, symbol=f"{name}window{i}", kind=KIND_MODULE,
            signature="", docstring="", imports=[], dependencies=[],
            first_line=line, last_line=line + window.count("\n"),
            body=window,
        ))
        offset += len(window)
    return out


def symbols_from_source(source: str, module: str) -> list[Symbol]:
    """Parse one module's source into `Symbol`s; an oversized *chunk* → windows.

    The single entry point both `ingest_code` and the tests/stub corpus use, so
    the split test is exercised on exactly the artifacts the store will hold.
    The guard is per-chunk (`MAX_CHUNK_CHARS`): a big module is still one chunk
    per symbol; only a definition whose own body exceeds the cap is windowed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _windowed(source, module)
    extractor = SymbolExtractor(module, source)
    extractor.visit(tree)
    symbols = list(extractor.symbols)
    run = _module_run(tree, module, source)
    if run is not None:
        symbols.append(run)

    bounded: list[Symbol] = []
    for sym in symbols:
        if len(sym.body) <= MAX_CHUNK_CHARS:
            bounded.append(sym)
        else:
            bounded.extend(_windowed(
                sym.body, module,
                symbol=None if sym.kind == KIND_MODULE else sym.symbol,
                line_offset=sym.first_line - 1))
    return bounded


def ingest_code(path: str | Path) -> list[Symbol]:
    """Walk a source tree, returning every module's symbols. Mirrors Day 6's
    `documents_from_markdown` at the corpus level — the second ingestion seam."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"code corpus not found: {root}")
    files: Iterable[Path] = sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in str(p))
    symbols: list[Symbol] = []
    for file in files:
        module = file.relative_to(root).as_posix()
        symbols.extend(symbols_from_source(file.read_text(encoding="utf-8"), module))
    return symbols


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "corpus_code"
    syms = ingest_code(root)
    print(f"{len(syms)} symbols from {root}")
    for s in syms[:15]:
        print(f"  {s.kind:<14} {s.doc_id:<60} {len(s.body):>5} chars")
