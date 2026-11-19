"""Keep documentation code maps and status vocabularies pointing at real code.

Two gates, both parsed from the pages themselves:

1. **Code map** — every opted-in page carries a ``| Stage | Entry symbol | Module |``
   table. Each row is resolved: the module must exist and must actually define the
   symbol. Anchors are symbols, not line numbers, because symbols survive
   refactors and line numbers do not.
2. **Status vocabulary** — the extraction page's examination-status table must
   match the statuses the backend actually sets, in both directions: a documented
   status that no code sets is drift, and a status the code sets that the table
   omits is drift. Known-legacy statuses may be mentioned in prose but must never
   appear as current rows.

Run it directly for a readable report, or through
``backend/tests/test_docs_anchors.py`` in the suite.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Pages that opt in to the code-map gate. Each MUST carry a parseable table.
PAGES: tuple[str, ...] = (
    "docs/EXTRACTION_PIPELINE.md",
    "docs/ONTOLOGY_CATALOG.md",
    "docs/STATE_BIOMARKERS.md",
)

TABLE_HEADER = "| Stage | Entry symbol | Module |"
STATUS_TABLE_HEADER = "| Examination status | Progress | Meaning |"

#: Statuses older rows can still carry and the docs may describe as history.
#: The pipeline never sets them; the table must not present them as current.
LEGACY_STATUSES: frozenset[str] = frozenset({"aggregating", "analyzing_text"})

#: Every place the pipeline assigns an examination status.
_STATUS_SETTERS: tuple[re.Pattern[str], ...] = (
    re.compile(r'update_examination_status\(\s*"([a-z_]+)"'),
    re.compile(r'extraction_status\s*=\s*"([a-z_]+)"'),
)

# Definition forms the repo actually uses: Python functions/classes/methods
# (top-level or indented) and TS/JS declarations.
_PYTHON_DEF = r"^\s*(?:async\s+)?(?:def|class)\s+{name}\b"
_TS_DEF = (
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+{name}\b"
)

_CELL_BACKTICK = re.compile(r"`([^`]+)`")


def _table_rows(text: str, header: str, columns: int) -> list[list[str]]:
    """Cells of the markdown table introduced by ``header`` (empty if absent)."""
    rows: list[list[str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == header:
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped.startswith("|"):
            break
        if set(stripped) <= {"|", "-", " ", ":"}:
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) >= columns:
            rows.append(cells)
    return rows


def rows_from_text(text: str) -> list[tuple[str, str]]:
    """Return ``(symbol, module)`` pairs from the page's code map."""
    pairs: list[tuple[str, str]] = []
    for cells in _table_rows(text, TABLE_HEADER, 3):
        symbol_match = _CELL_BACKTICK.search(cells[1])
        module_match = _CELL_BACKTICK.search(cells[2])
        if symbol_match and module_match:
            pairs.append((symbol_match.group(1), module_match.group(1)))
    return pairs


def documented_statuses(text: str) -> list[str]:
    """Examination statuses the page presents as current (table rows only)."""
    statuses: list[str] = []
    for cells in _table_rows(text, STATUS_TABLE_HEADER, 3):
        match = _CELL_BACKTICK.search(cells[0])
        if match:
            statuses.append(match.group(1))
    return statuses


def _defines(source: str, symbol: str) -> bool:
    """Does ``source`` define ``symbol`` (dotted ``Class.method`` supported)?"""
    owners: list[str] = []
    member = symbol
    if "." in symbol:
        owners = [symbol.rsplit(".", 1)[0]]
        member = symbol.rsplit(".", 1)[1]
    for name in [*owners, member]:
        if not (
            re.search(_PYTHON_DEF.format(name=re.escape(name)), source, re.M)
            or re.search(_TS_DEF.format(name=re.escape(name)), source, re.M)
        ):
            return False
    return True


def violations(rows: list[tuple[str, str]], repo: Path) -> list[str]:
    """Check ``(symbol, module)`` rows against the tree; return failures."""
    problems: list[str] = []
    for symbol, module in rows:
        path = repo / module
        if not path.is_file():
            problems.append(f"{symbol}: module not found — {module}")
            continue
        if not _defines(path.read_text(encoding="utf-8"), symbol):
            problems.append(f"{symbol}: not defined in {module}")
    return problems


def code_set_statuses(app_dir: Path) -> set[str]:
    """Examination statuses the backend actually assigns."""
    found: set[str] = set()
    for path in app_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for pattern in _STATUS_SETTERS:
            found.update(pattern.findall(source))
    return found


def status_violations(documented: list[str], assigned: set[str]) -> list[str]:
    """Both drift directions plus the legacy-status guard."""
    problems: list[str] = []
    for status in documented:
        if status not in assigned:
            problems.append(f"documented status {status!r} is never set by the backend")
    for status in sorted(assigned - set(documented)):
        problems.append(f"backend sets status {status!r} but the status table omits it")
    for status in sorted(LEGACY_STATUSES & set(documented)):
        problems.append(
            f"legacy status {status!r} is presented as current — mention it in prose only"
        )
    return problems


def check_page(repo: Path, page: str) -> list[str]:
    """One opted-in page: it exists and its code map resolves."""
    path = repo / page
    if not path.is_file():
        return [f"{page} is missing"]
    rows = rows_from_text(path.read_text(encoding="utf-8"))
    if not rows:
        return [f"{page} has no parseable code map table"]
    return violations(rows, repo)


def check(repo: Path) -> list[str]:
    """Run every gate. Empty list means the pages are honest."""
    problems: list[str] = []
    for page in PAGES:
        problems.extend(check_page(repo, page))

    extraction = repo / "docs/EXTRACTION_PIPELINE.md"
    documented = (
        documented_statuses(extraction.read_text(encoding="utf-8")) if extraction.is_file() else []
    )
    if not documented:
        problems.append("docs/EXTRACTION_PIPELINE.md has no parseable status table")
    else:
        problems.extend(status_violations(documented, code_set_statuses(repo / "backend/app")))
    return problems


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    problems = check(repo)
    if problems:
        print("documentation anchors are stale:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    anchors = sum(len(rows_from_text((repo / page).read_text(encoding="utf-8"))) for page in PAGES)
    statuses = documented_statuses(
        (repo / "docs/EXTRACTION_PIPELINE.md").read_text(encoding="utf-8")
    )
    print(f"docs gates OK — {len(PAGES)} pages, {anchors} code-map rows, {len(statuses)} statuses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
