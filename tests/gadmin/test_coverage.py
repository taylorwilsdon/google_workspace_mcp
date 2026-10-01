"""The checked-in admin coverage manifest cannot overstate the registered surface."""

import json
from pathlib import Path

from gadmin.registry import excluded_operations, iter_operations

REPORT = Path(__file__).parents[2] / "docs" / "admin-capability-coverage.md"
REQUIRED_FAMILIES = {
    "directory",
    "datatransfer",
    "licensing",
    "reports",
    "vault",
    "alertcenter",
    "cloudidentity",
    "groupssettings",
    "chromemanagement",
    "chromepolicy",
    "accesscontextmanager",
    "access-policies",
    "contactdelegation",
    "gmail",
}
# Families with a pinned, exhaustive method inventory in the registry.
PINNED_FAMILIES = {
    ("admin", "directory_v1"): "directory",
    ("admin", "datatransfer_v1"): "datatransfer",
    ("licensing", "v1"): "licensing",
    ("admin", "reports_v1"): "reports",
    ("vault", "v1"): "vault",
    ("alertcenter", "v1beta1"): "alertcenter",
    ("groupssettings", "v1"): "groupssettings",
    ("cloudidentity", "v1"): "cloudidentity",
    ("cloudidentity", "v1beta1"): "cloudidentity",
    ("chromemanagement", "v1"): "chromemanagement",
    ("chromepolicy", "v1"): "chromepolicy",
    ("accesscontextmanager", "v1"): "accesscontextmanager",
    ("admin", "contacts_v1"): "contactdelegation",
    ("gmail", "v1"): "gmail",
}
# Operation ID prefixes that belong to another family's manifest entry; the
# longest matching prefix wins.
PREFIX_FAMILIES = {"admin.contacts": "contactdelegation", "admin": "directory"}


def coverage():
    text = REPORT.read_text(encoding="utf-8")
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def test_every_registered_operation_has_an_accurate_coverage_entry():
    report = coverage()
    registered = {spec.id: spec for spec in iter_operations()}
    documented = {}
    for family, entry in report.items():
        assert entry["reference"].startswith(
            ("https://developers.google.com/", "https://cloud.google.com/")
        )
        assert entry["status"] in ("complete", "partial", "pending")
        assert bool(entry["remaining_methods"]) == (entry["status"] != "complete")
        for method in entry["registered_methods"]:
            assert method not in documented
            documented[method] = family
    assert set(report) == REQUIRED_FAMILIES
    assert set(documented) == set(registered)
    # Operation IDs keep Google's casing ("groupsSettings.groups.get").
    for method, family in documented.items():
        parts = method.casefold().split(".")
        prefixes = [".".join(parts[:2]), parts[0]]
        owner = next(
            (PREFIX_FAMILIES[p] for p in prefixes if p in PREFIX_FAMILIES), None
        )
        assert family == (owner or parts[0]), method
    for family in (
        "directory",
        "reports",
        "vault",
        "alertcenter",
        "groupssettings",
        "cloudidentity",
        "chromemanagement",
        "chromepolicy",
        "accesscontextmanager",
        "gmail",
    ):
        assert report[family]["status"] == "partial", family
    assert report["contactdelegation"]["status"] == "complete"
    assert all(
        not entry["registered_methods"]
        for entry in report.values()
        if entry["status"] == "pending"
    )


def test_pinned_families_list_every_excluded_method():
    report = coverage()
    excluded = {family: [] for family in PINNED_FAMILIES.values()}
    for exclusion in excluded_operations():
        family = PINNED_FAMILIES[(exclusion.service, exclusion.version)]
        excluded[family].append(exclusion.id)

    for family, methods in excluded.items():
        assert report[family]["remaining_methods"] == sorted(methods), family
    assert report["datatransfer"]["status"] == "complete"
    assert report["licensing"]["status"] == "partial"


def test_docs_name_every_exposed_admin_tool():
    from gadmin.admin_tools import _TOOL_SERVICES

    readme = (REPORT.parents[1] / "README.md").read_text(encoding="utf-8")
    admin_section = readme.split("## Workspace administration", 1)[1].split("\n## ")[0]
    report_intro = REPORT.read_text(encoding="utf-8").split("## Coverage manifest")[0]

    for tool in _TOOL_SERVICES:
        assert f"`{tool}`" in admin_section, tool
    for tool in ("admin_operation", "confirm_admin_operation"):
        assert f"`{tool}`" in report_intro, tool
    # The report must not describe registered operations as internal-only.
    assert "not necessarily an independent MCP tool" not in report_intro
