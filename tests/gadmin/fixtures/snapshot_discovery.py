"""Write the pinned discovery snapshots used by the admin coverage tests.

Usage: python tests/gadmin/fixtures/snapshot_discovery.py <discovery.json>...

Each argument is a public, unauthenticated Google Discovery document saved from
its ``source`` URL. The snapshot keeps every method with its HTTP verb, path,
parameters, scopes, and request/response schema names, plus a flattened view of
every schema, so tests can check the registry against the full method list.
This script is run by hand when the pinned revision is reviewed; nothing reads
discovery at tool-call time.
"""

import json
import sys
from datetime import date
from pathlib import Path

SOURCES = {
    ("admin", "directory_v1"): (
        "https://admin.googleapis.com/$discovery/rest?version=directory_v1"
    ),
    ("admin", "datatransfer_v1"): (
        "https://admin.googleapis.com/$discovery/rest?version=datatransfer_v1"
    ),
    ("admin", "reports_v1"): (
        "https://admin.googleapis.com/$discovery/rest?version=reports_v1"
    ),
    ("licensing", "v1"): "https://licensing.googleapis.com/$discovery/rest?version=v1",
    ("vault", "v1"): "https://vault.googleapis.com/$discovery/rest?version=v1",
    ("alertcenter", "v1beta1"): (
        "https://alertcenter.googleapis.com/$discovery/rest?version=v1beta1"
    ),
    ("groupssettings", "v1"): (
        "https://groupssettings.googleapis.com/$discovery/rest?version=v1"
    ),
    ("cloudidentity", "v1"): (
        "https://cloudidentity.googleapis.com/$discovery/rest?version=v1"
    ),
    ("cloudidentity", "v1beta1"): (
        "https://cloudidentity.googleapis.com/$discovery/rest?version=v1beta1"
    ),
    ("chromemanagement", "v1"): (
        "https://chromemanagement.googleapis.com/$discovery/rest?version=v1"
    ),
    ("chromepolicy", "v1"): (
        "https://chromepolicy.googleapis.com/$discovery/rest?version=v1"
    ),
    ("accesscontextmanager", "v1"): (
        "https://accesscontextmanager.googleapis.com/$discovery/rest?version=v1"
    ),
    # Google publishes no discovery document for Contact Delegation; the
    # snapshot is taken from the hand-written gadmin/discovery copy.
    ("admin", "contacts_v1"): (
        "https://developers.google.com/workspace/admin/contact-delegation/reference/rest"
    ),
    ("gmail", "v1"): "https://gmail.googleapis.com/$discovery/rest?version=v1",
}
_PARAM_KEYS = ("location", "type", "required", "enum", "pattern")


def _type(prop: dict) -> str:
    if "$ref" in prop:
        return prop["$ref"]
    if prop.get("type") == "array":
        items = prop.get("items", {})
        return "array:" + (items.get("$ref") or items.get("type", "any"))
    return prop.get("type", "any")


def _methods(resources: dict, out: dict) -> None:
    for resource in resources.values():
        for method in resource.get("methods", {}).values():
            out[method["id"]] = {
                "httpMethod": method["httpMethod"],
                "parameters": {
                    name: {k: p[k] for k in _PARAM_KEYS if k in p}
                    for name, p in sorted(method.get("parameters", {}).items())
                },
                "path": method["path"],
                "request": method.get("request", {}).get("$ref"),
                "response": method.get("response", {}).get("$ref"),
                "scopes": sorted(method.get("scopes", [])),
            }
        _methods(resource.get("resources", {}), out)


def snapshot(doc: dict) -> dict:
    methods: dict = {}
    _methods(doc["resources"], methods)
    return {
        "name": doc["name"],
        "version": doc["version"],
        "revision": doc["revision"],
        "source": SOURCES[(doc["name"], doc["version"])],
        "retrieved": date.today().isoformat(),
        "methods": dict(sorted(methods.items())),
        "schemas": {
            name: {k: _type(p) for k, p in sorted(s.get("properties", {}).items())}
            for name, s in sorted(doc["schemas"].items())
        },
    }


if __name__ == "__main__":
    for path in sys.argv[1:]:
        data = snapshot(json.loads(Path(path).read_text(encoding="utf-8")))
        target = Path(__file__).parent / (
            f"{data['name']}_{data['version']}_discovery.json"
        )
        target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(
            f"{target.name}: revision {data['revision']}, {len(data['methods'])} methods"
        )
