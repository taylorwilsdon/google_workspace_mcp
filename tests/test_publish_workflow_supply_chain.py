"""Findings 15 and 46: the publish workflow must not execute unverified input.

The publish job holds a PyPI publishing identity and an MCP Registry OIDC identity, so
anything it fetches and runs inherits both.

15: the schema validator read `$schema` out of `server.json` -- a repository file --
    and fetched that URL from the runner. That is an SSRF with the runner's network
    position, and it let the validated-against contract be chosen by whoever could
    edit the file.
46: `mcp-publisher` was downloaded from `releases/latest` and piped straight into
    `tar`, with no version pin and no integrity check, so extraction happened before
    anything could be verified.
"""

from __future__ import annotations

import json
import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(
    REPO_ROOT, ".github", "workflows", "publish-mcp-registry.yml"
)
SCHEMA_PATH = os.path.join(REPO_ROOT, ".github", "schemas", "server.schema.json")
SERVER_JSON_PATH = os.path.join(REPO_ROOT, "server.json")

# A 40-hex commit SHA. Tags and branches are mutable, so an action referenced by name
# can change under a job that publishes releases.
_SHA_PINNED = re.compile(r"^[^@]+@[0-9a-f]{40}(?:\s*#.*)?$")


@pytest.fixture(scope="module")
def workflow() -> dict:
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def steps(workflow) -> list:
    return workflow["jobs"]["publish"]["steps"]


@pytest.fixture(scope="module")
def run_script(steps) -> str:
    """All shell and inline-Python the job executes, concatenated."""
    return "\n".join(step["run"] for step in steps if "run" in step)


class TestVendoredSchema:
    """Finding 15: validate against a file in the repository, not a fetched URL."""

    def test_schema_is_vendored(self):
        assert os.path.isfile(SCHEMA_PATH), (
            "the server.json schema must be committed so CI does not fetch it"
        )

    def test_vendored_schema_matches_the_declared_id(self):
        """server.json's $schema and the vendored $id must agree.

        If they drift, the file is validated against a different contract than it
        claims to follow.
        """
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
        with open(SERVER_JSON_PATH, encoding="utf-8") as f:
            server = json.load(f)

        assert server["$schema"] == schema["$id"]

    def test_workflow_points_at_the_vendored_path(self, workflow):
        assert (
            workflow["env"]["SERVER_SCHEMA_PATH"]
            == ".github/schemas/server.schema.json"
        )

    def test_workflow_does_not_fetch_the_schema(self, run_script):
        # `requests` was the fetch mechanism; neither it nor a curl of the schema host
        # belongs in the validation step.
        assert "requests.get" not in run_script
        assert "static.modelcontextprotocol.io" not in run_script

    def test_workflow_reads_the_schema_from_disk(self, run_script):
        assert "SERVER_SCHEMA_PATH" in run_script


class TestPublisherIntegrity:
    """Finding 46: pin the version and verify the archive before extracting."""

    def test_version_is_pinned(self, workflow):
        version = workflow["env"]["MCP_PUBLISHER_VERSION"]
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), version

    def test_latest_is_not_used(self, run_script):
        assert "releases/latest" not in run_script

    def test_download_url_uses_the_pinned_version(self, run_script):
        assert "releases/download/v${MCP_PUBLISHER_VERSION}" in run_script

    @pytest.mark.parametrize(
        "var",
        ["MCP_PUBLISHER_SHA256_LINUX_AMD64", "MCP_PUBLISHER_SHA256_LINUX_ARM64"],
    )
    def test_checksums_are_declared(self, workflow, var):
        digest = workflow["env"][var]
        assert re.fullmatch(r"[0-9a-f]{64}", digest), digest

    def test_checksum_is_verified(self, run_script):
        assert "sha256sum --check --strict" in run_script

    def test_archive_is_not_piped_into_tar(self, run_script):
        """Piping runs the extraction before anything can be verified."""
        assert not re.search(r"curl[^\n]*\|\s*tar", run_script)

    def test_verification_precedes_extraction(self, run_script):
        """Ordering is the whole control: a checksum after `tar` proves nothing."""
        check_at = run_script.index("sha256sum --check")
        extract_at = run_script.index("tar xzf")
        assert check_at < extract_at

    def test_download_requires_https(self, run_script):
        assert "--proto '=https'" in run_script


class TestJobHardening:
    def test_workflow_permissions_default_to_none(self, workflow):
        assert workflow["permissions"] == {}

    def test_job_permissions_are_minimal(self, workflow):
        assert workflow["jobs"]["publish"]["permissions"] == {
            "contents": "read",
            "id-token": "write",
        }

    def test_every_action_is_pinned_to_a_commit_sha(self, steps):
        unpinned = [
            step["uses"]
            for step in steps
            if "uses" in step and not _SHA_PINNED.match(step["uses"])
        ]
        assert unpinned == [], (
            "these actions are referenced by a mutable tag or branch; pin them to a "
            f"commit SHA: {unpinned}"
        )

    def test_install_step_fails_fast(self, run_script):
        """Without `set -e` a failed download would fall through to the next command."""
        assert "set -euo pipefail" in run_script
