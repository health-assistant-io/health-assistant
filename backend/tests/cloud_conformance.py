"""Cross-language conformance wall for the family cloud-connector contract.

ADR-0052 / plan 52. The Python implementations live beside this file
(``cloud_providers.py``, ``cloud_calendar.py``, ``cloud_files.py``) and the
fixture table is ``cloud-connectors.fixtures.json``. Every family host that
vendors the contract runs :func:`run` (or the CLI below) against the SAME
fixtures: implementations may differ per language, outputs may not
(ADR-0040 clause 22).

The runner is pure: no network, no credentials, no clock dependency. Wire
cases call the private provider mappers directly because the transports
themselves add HTTP behaviour the fixture is not asserting.
"""

# ruff: noqa: E402 -- the module-path bootstrap below must run before imports

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# Canonical location (the family contract canonical) has the modules beside this file. A
# vendored host copy lives under `<backend>/tests/`, where the engines are in
# `<backend>/app/extensions/cloud/` and the fixtures in `<backend>/tests/fixtures/`.
ROOT = Path(__file__).resolve().parent
for _candidate in (ROOT, ROOT.parent / "app" / "extensions" / "cloud"):
    if (_candidate / "cloud_calendar.py").is_file() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from cloud_calendar import (
    CalendarError,
    _event_to_google,
    _event_to_graph,
    ConflictPolicy,
    DeletionPolicy,
    EventLink,
    LocalEvent,
    SyncDirection,
    _google_to_event,
    _graph_to_event,
    event_from_dict,
    event_hash,
    event_to_dict,
    expand_event,
    parse_ics,
    to_ics,
)
from cloud_files import FileError, join_path
from cloud_providers import (
    default_catalog,
    parse_catalog,
    provider_rows,
    scope_growth,
    scope_presets_for,
)

FIXTURES = (
    ROOT / "cloud-connectors.fixtures.json"
    if (ROOT / "cloud-connectors.fixtures.json").is_file()
    else ROOT / "fixtures" / "cloud-connectors.fixtures.json"
)
CATALOG = (
    ROOT / "cloud-providers.json"
    if (ROOT / "cloud-providers.json").is_file()
    else ROOT.parent / "app" / "extensions" / "cloud" / "cloud-providers.json"
)


class ConformanceError(AssertionError):
    """One fixture row failed; the message names the row and the field."""


def _fail(where: str, detail: str) -> None:
    raise ConformanceError(f"{where}: {detail}")


def _check(where: str, condition: bool, detail: str) -> None:
    if not condition:
        _fail(where, detail)


def _subset(where: str, actual: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    for key, value in expected.items():
        _check(where, key in actual, f"missing key {key!r}")
        if actual[key] != value:
            _fail(where, f"{key!r} expected {value!r}, got {actual[key]!r}")


def _catalog(document: Mapping[str, Any]) -> None:
    catalog = parse_catalog(document)
    where = "catalog"
    expected_order = document["order"]
    _check(
        where,
        list(catalog.order) == expected_order,
        f"order {catalog.order!r} != {expected_order!r}",
    )
    _check(
        where,
        list(catalog.ids_for_service("calendar")) == document["calendarProviders"],
        "calendar provider set drifted from the fixture",
    )
    _check(
        where,
        list(catalog.ids_for_service("files")) == document["filesProviders"],
        "files provider set drifted from the fixture",
    )
    for provider_id, services in document["scopes"].items():
        provider = catalog.get(provider_id)
        for service, presets in services.items():
            spec = provider.service(service)
            for preset, scopes in presets.items():
                _check(
                    f"{where}.{provider_id}.{service}.{preset}",
                    list(scope_presets_for(provider, service)[preset]) == scopes,
                    "scope preset drifted from the fixture",
                )
            del spec
    client_env = document.get("clientIdEnv")
    if client_env:
        from cloud_providers import client_id_env

        _check(
            "catalog.clientIdEnv.template",
            client_env["template"] == "{PREFIX}_{ID}_CLIENT_ID",
            f"template drifted: {client_env['template']!r}",
        )
        for case in client_env.get("cases", []):
            got = client_id_env(case["id"], prefix=case["prefix"])
            _check(
                f"catalog.clientIdEnv.{case['id']}.{case['prefix']}",
                got == case["expect"],
                f"expected {case['expect']!r}, got {got!r}",
            )

    for index, case in enumerate(document.get("scopeGrowth", [])):
        added, removed = scope_growth(case["granted"], case["requested"])
        _check(
            f"{where}.scopeGrowth[{index}]",
            list(added) == case["added"] and list(removed) == case["removed"],
            f"got added={list(added)!r} removed={list(removed)!r}",
        )

    # Rev 6: the setupFields descriptor is the bind-form contract.
    setup = document.get("setupFields")
    if setup:
        from cloud_providers import SETUP_FIELD_KINDS

        _check(
            "catalog.setupFields.kinds",
            list(SETUP_FIELD_KINDS) == setup["requiredKinds"],
            f"kind vocabulary drifted: {list(SETUP_FIELD_KINDS)!r}",
        )
        rows = provider_rows(default_catalog(), "calendar")
        for row in rows:
            spec = default_catalog().get(row["id"]).service("calendar")
            want = setup["perProtocol"].get(spec.protocol)
            if not want:
                continue
            where = f"catalog.setupFields.{row['id']}"
            _check(
                where,
                [field["kind"] for field in row["setupFields"]] == want["kinds"],
                f"kinds {[f['kind'] for f in row['setupFields']]} != {want['kinds']}",
            )
            secret_kinds = sorted(
                {
                    field["authKind"]
                    for field in row["setupFields"]
                    if field["kind"] == "secret" and field.get("authKind")
                }
            )
            _check(
                where,
                secret_kinds == sorted(want["secretAuthKinds"]),
                f"secret authKinds {secret_kinds} != {sorted(want['secretAuthKinds'])}",
            )
            if setup.get("everyFieldHasLabelKey"):
                for field in row["setupFields"]:
                    _check(
                        where,
                        bool(field.get("labelKey")),
                        f"field {field.get('key')!r} has no labelKey",
                    )


def _ics(fixtures: Mapping[str, Any]) -> None:
    for index, case in enumerate(fixtures.get("parse", [])):
        where = f"ics.parse[{index}:{case['name']}]"
        events = parse_ics(case["ics"])
        _check(where, len(events) == 1, f"expected 1 event, got {len(events)}")
        _subset(where, event_to_dict(events[0]), case["expect"])

    for index, case in enumerate(fixtures.get("roundtrip", [])):
        where = f"ics.roundtrip[{index}]"
        event = event_from_dict(case["event"])
        parsed = parse_ics(to_ics([event]))
        _check(where, len(parsed) == 1, f"expected 1 event, got {len(parsed)}")
        actual = event_to_dict(parsed[0])
        expected = event_to_dict(event)
        for key, value in expected.items():
            if key in ("remoteId", "etag", "updatedAt"):
                continue
            _check(where, actual[key] == value, f"{key!r} expected {value!r}, got {actual[key]!r}")

    for index, case in enumerate(fixtures.get("recurrence", [])):
        where = f"ics.recurrence[{index}:{case['name']}]"
        event = event_from_dict(case["event"])
        window = case["window"]
        occurrences = expand_event(
            event,
            date.fromisoformat(window[0]),
            date.fromisoformat(window[1]),
        )
        days = [item.start_day.isoformat() for item in occurrences]
        _check(where, days == case["expectStartDays"], f"got {days!r}")


def _local(record: Mapping[str, Any]) -> LocalEvent:
    event = event_from_dict(record["event"])
    return LocalEvent(
        local_id=record["localId"],
        event=event,
        updated_at=datetime.fromisoformat(record["updatedAt"].replace("Z", "+00:00")),
        content_hash=record.get("contentHash") or record.get("localHash"),
        deleted=bool(record.get("deleted")),
    )


#: Fixture hash placeholders: a literal string is a literal pin; `$local` /
#: `$remote` resolve to the hash of THIS row's local / remote event, and
#: `$none` means "unpinned" (the first-reconcile case). Without them a fixture
#: cannot express "this side is unchanged", so every row degenerated into the
#: both-changed path (the E3 tail the wall never caught).
def _resolve_pin(
    value: Any,
    *,
    local: Mapping[str, Any],
    remote: Mapping[str, Any],
) -> str | None:
    if value is None:
        return None
    if value == "$none":
        return None
    if value == "$local":
        return event_hash(event_from_dict(local["event"]))
    if value == "$remote":
        return event_hash(event_from_dict(remote))
    return str(value)


def _link(record: Mapping[str, Any], row: Mapping[str, Any]) -> EventLink:
    locals_by_id = {entry["localId"]: entry for entry in row.get("local", [])}
    remotes = row.get("remote", [])
    local_row = locals_by_id.get(record["localId"], {"event": {"uid": record["uid"], "start": "2026-01-01", "summary": ""}})
    # the remote side is keyed by its write handle (remoteId), not its uid
    remote_row = next(
        (entry for entry in remotes if record.get("remoteId") and entry.get("remoteId") == record["remoteId"]),
        next((entry for entry in remotes if entry["uid"] == record["uid"]),
             {"uid": record["uid"], "start": "2026-01-01", "summary": ""}),
    )
    return EventLink(
        local_id=record["localId"],
        uid=record["uid"],
        remote_id=record.get("remoteId"),
        etag=record.get("etag"),
        local_hash=_resolve_pin(record.get("localHash"), local=local_row, remote=remote_row),
        remote_hash=_resolve_pin(record.get("remoteHash"), local=local_row, remote=remote_row),
        updated_at=datetime.fromisoformat(record["updatedAt"].replace("Z", "+00:00")),
    )


def _reconcile(fixtures: Mapping[str, Any]) -> None:
    from cloud_calendar import reconcile

    for index, case in enumerate(fixtures.get("reconcile", [])):
        where = f"reconcile[{index}:{case['name']}]"
        args = case.get("args", {})
        accept_categories = args.get("acceptCategories")
        accept = None
        if accept_categories is not None:
            allowed = {str(value) for value in accept_categories}
            accept = lambda event, allowed=allowed: bool(set(event.categories) & allowed)  # noqa: E731
        window = args.get("window")
        plan = reconcile(
            [_local(record) for record in case.get("local", [])],
            [event_from_dict(record) for record in case.get("remote", [])],
            [_link(record, case) for record in case.get("links", [])],
            direction=SyncDirection(args.get("direction", "two-way")),
            conflicts=ConflictPolicy(args.get("conflicts", "skip")),
            deletions=DeletionPolicy(args.get("deletions", "keep-local")),
            accept=accept,
            remote_writable=bool(args.get("remoteWritable", True)),
            now=datetime(2026, 11, 3, 13, 0, tzinfo=UTC),
            window=(date.fromisoformat(window[0]), date.fromisoformat(window[1]))
            if window
            else None,
        )
        actual = [
            {
                key: value
                for key, value in {
                    "kind": action.kind.value,
                    "uid": action.uid,
                    "localId": action.local_id,
                    "remoteId": action.remote_id,
                    "etag": action.event.etag if action.event else None,
                }.items()
                if value is not None
            }
            for action in plan.actions
        ]
        expected = case["expect"]["actions"]
        _check(
            where,
            len(actual) == len(expected),
            f"actions expected {len(expected)} rows, got {len(actual)}: {actual!r}",
        )
        for position, (want, got) in enumerate(zip(expected, actual, strict=False)):
            for key, value in want.items():
                _check(
                    f"{where}.actions[{position}]",
                    got.get(key) == value,
                    f"{key!r} expected {value!r}, got {got.get(key)!r}",
                )
        _check(
            where,
            plan.skipped == case["expect"]["skipped"],
            f"skipped expected {case['expect']['skipped']}, got {plan.skipped}",
        )


def _dav_lookup(body: Mapping[str, Any], dotted: str):
    node, _, rest = dotted.partition(".")
    value = body.get(node)
    return value if not rest else _dav_lookup(value or {}, rest)


def _files(fixtures: Mapping[str, Any]) -> None:
    listing = fixtures.get("webdavList")
    if listing:
        # E7: this row was shipped and never executed. Run it through the real
        # WebDAV parser via an injected transport — no network.
        import asyncio

        import httpx

        from cloud_files import WebDavTransport

        xml = listing["xml"]

        def handler(request):  # noqa: ANN001
            return httpx.Response(207, text=xml)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = WebDavTransport(client, listing["baseUrl"])

        async def run_list():
            try:
                return await transport.list(listing.get("path", ""))
            finally:
                await client.aclose()

        page = asyncio.run(run_list())
        actual = [
            {
                "name": row.name,
                "path": row.path,
                "isDir": row.is_dir,
                "size": row.size,
                "etag": row.etag,
            }
            for row in page.files
        ]
        _check("files.webdavList", actual == listing["expect"], f"got {actual!r}")
    for index, case in enumerate(fixtures.get("paths", [])):
        where = f"files.paths[{index}]"
        _check(where, join_path(*case["parts"]) == case["expect"], "path join drifted")
    for index, case in enumerate(fixtures.get("traversal", [])):
        where = f"files.traversal[{index}]"
        try:
            join_path(*case["parts"])
        except FileError as error:
            _check(
                where,
                error.code == case["expectError"],
                f"expected {case['expectError']!r}, got {error.code!r}",
            )
        else:
            _fail(where, "expected a typed FileError")


def _wire(fixtures: Mapping[str, Any]) -> None:
    google = fixtures.get("googleList")
    if google:
        event = _google_to_event(google["response"]["items"][0])
        expect = google["expect"]
        _check("wire.googleList", event.uid == expect["uid"], "google uid drifted")
        _check(
            "wire.googleList",
            event_to_dict(event)["start"] == expect["start"],
            "google start drifted",
        )
        if "remoteId" in expect:
            _check(
                "wire.googleList",
                event.remote_id == expect["remoteId"],
                "google remoteId (the write handle) drifted from the identity uid",
            )
        if "count" in expect:
            _check(
                "wire.googleList",
                len(google["response"]["items"]) == expect["count"],
                "google item count drifted",
            )
        if "nextSyncToken" in expect:
            _check(
                "wire.googleList",
                google["response"].get("nextSyncToken") == expect["nextSyncToken"],
                "google nextSyncToken drifted",
            )
    graph = fixtures.get("graphList")
    if graph:
        event = _graph_to_event(graph["response"]["value"][0])
        expect = graph["expect"]
        _check("wire.graphList", event.uid == expect["uid"], "graph uid drifted")
        _check(
            "wire.graphList",
            event_to_dict(event)["start"] == expect["start"],
            "graph start drifted",
        )
        _check(
            "wire.graphList",
            list(event.categories) == expect["categories"],
            "graph categories drifted",
        )
        if "count" in expect:
            _check(
                "wire.graphList",
                len(graph["response"]["value"]) == expect["count"],
                "graph item count drifted",
            )

    def dotted(body: Mapping[str, Any], path: str):
        node, _, rest = path.partition(".")
        value = body.get(node)
        return value if not rest else dotted(value or {}, rest)

    from cloud_calendar import GraphCalendarTransport

    for index, case in enumerate(fixtures.get("graphUrls", []) or []):
        transport = GraphCalendarTransport(
            client=None, calendar_id=case["calendarId"], user=case["user"]
        )
        _check(
            f"wire.graphUrls[{index}]",
            transport._base() == case["expect"],
            f"url built {transport._base()!r}, expected {case['expect']!r}",
        )

    def discover(protocol: str, payload: Mapping[str, Any]):
        import asyncio

        import httpx

        from cloud_calendar import GoogleCalendarTransport, GraphCalendarTransport

        def handler(request):  # noqa: ANN001
            return httpx.Response(200, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = (
            GoogleCalendarTransport(client, "primary")
            if protocol == "google-calendar"
            else GraphCalendarTransport(client, "cal-1")
        )

        async def run_discover():
            try:
                return await transport.discover_calendars()
            finally:
                await client.aclose()

        return asyncio.run(run_discover())

    for group, protocol in (
        ("googleCalendarList", "google-calendar"),
        ("graphCalendarList", "graph-calendar"),
    ):
        case = fixtures.get(group)
        if not case:
            continue
        rows = discover(protocol, case["response"])
        _check(f"wire.{group}", len(rows) == len(case["expect"]), f"got {rows!r}")
        for index, (got, want) in enumerate(zip(rows, case["expect"])):
            for key, value in want.items():
                _check(
                    f"wire.{group}[{index}]",
                    got.get(key) == value,
                    f"{key!r} expected {value!r}, got {got.get(key)!r}",
                )

    for group, writer in (("googleWrite", _event_to_google), ("graphWrite", _event_to_graph)):
        for index, case in enumerate(fixtures.get(group, []) or []):
            where = f"wire.{group}[{index}:{case['name']}]"
            event = event_from_dict(case["event"])
            if "expectError" in case:
                try:
                    writer(event)
                except CalendarError as error:
                    _check(
                        where,
                        error.code == case["expectError"],
                        f"expected {case['expectError']!r}, got {error.code!r}",
                    )
                else:
                    _fail(where, "expected a typed CalendarError")
                continue
            body = writer(event)
            for key, value in case["expect"].items():
                got = dotted(body, key)
                _check(where, got == value, f"{key!r} expected {value!r}, got {got!r}")


def _attribution(fixtures: Mapping[str, Any]) -> None:
    group = fixtures.get("attribution")
    if not group:
        return
    from cloud_calendar import SOURCE_CATEGORY, is_attributable

    _check(
        "attribution.sourceCategory",
        SOURCE_CATEGORY == group["sourceCategory"],
        f"marker drifted: {SOURCE_CATEGORY!r}",
    )
    namespaces = tuple(group.get("namespaces", ()))
    for index, case in enumerate(group.get("cases", [])):
        where = f"attribution[{index}:{case['name']}]"
        event = event_from_dict(case["event"])
        got = is_attributable(event, namespaces=namespaces)
        _check(
            where,
            got is case["attributable"],
            f"expected attributable={case['attributable']}, got {got}",
        )


def run(fixtures_path: Path | None = None, catalog_path: Path | None = None) -> int:
    fixtures = json.loads(Path(fixtures_path or FIXTURES).read_text(encoding="utf-8"))
    catalog_document = json.loads(Path(catalog_path or CATALOG).read_text(encoding="utf-8"))
    _check("root", fixtures.get("contract") == "cloud-connectors", "wrong fixture contract")
    _catalog({**catalog_document, **fixtures["catalog"]})
    # The catalog's own provider rows must match the fixture's provider sets.
    built = default_catalog()
    _check(
        "catalog.default",
        list(built.ids_for_service("calendar")) == fixtures["catalog"]["calendarProviders"],
        "default catalog calendar list drifted",
    )
    _check(
        "catalog.default",
        list(built.ids_for_service("files")) == fixtures["catalog"]["filesProviders"],
        "default catalog files list drifted",
    )
    _ics(fixtures["ics"])
    _reconcile(fixtures)
    _attribution(fixtures)
    _files(fixtures["files"])
    _wire(fixtures["wire"])
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    fixtures = Path(argv[0]) if argv else None
    try:
        run(fixtures)
    except ConformanceError as error:
        print(f"cloud-connectors conformance FAILED: {error}", file=sys.stderr)
        return 1
    print("cloud-connectors conformance: all rows green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
