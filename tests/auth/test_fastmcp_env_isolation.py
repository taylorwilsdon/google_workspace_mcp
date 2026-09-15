"""OAuthConfig's environment writes must not outlive the test that makes them.

`OAuthConfig._apply_fastmcp_google_env` writes FASTMCP_SERVER_AUTH* into
`os.environ` when they are absent and a client_id is configured. `monkeypatch`
restores only what it set itself, so those writes survive the test that caused
them, and every later test runs against configuration it did not set.

Nothing fails as a result today -- the leak is silent, which is why it lasted.

TWO PATHS REACH THAT WRITE, and they mask each other, so measuring this is easy
to get wrong in both directions:

  1. IMPORT TIME. `core/config.py` evaluates `is_oauth21_enabled()` at module
     level, building the memoised config during collection. It fires only when
     a credential is already resolvable -- an exported GOOGLE_OAUTH_CLIENT_ID,
     or the default repo-root client_secret.json. No FIXTURE can reach it,
     because none exists yet. (A module-level statement in tests/conftest.py
     could; that is deliberately not done here, since it would hide the
     production defect rather than contain a test-suite one.)

  2. TEST TIME. A test sets GOOGLE_OAUTH_CLIENT_ID and builds an OAuthConfig.
     It fires when path 1 did NOT, because the names are then still absent.

CI has no credentials, so CI runs path 2, and path 2 is what the fixture
contains. On a developer machine with a credential present, path 1 fires first
and every later `_set_if_absent` is a no-op -- so measuring there shows a
fixture apparently doing nothing. The probes below therefore neutralise BOTH
credential sources in the child environment, variables and file alike.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from auth.oauth_config import OAuthConfig
from tests.conftest import _FASTMCP_ENV_VARS

PINNED = "pinned-by-test"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Environment names this suite is KNOWN to leak, that this change does not fix.
# Listed rather than filtered out, so the condition is recorded and reviewable
# instead of invisible: a probe that matched only FASTMCP_* names would be
# structurally unable to see any of these, or any future leak under a name
# nobody has enumerated yet.
#
#   OAUTHLIB_INSECURE_TRANSPORT   auth/google_auth.py:705,900
#   OAUTHLIB_RELAX_TOKEN_SCOPE    auth/google_auth.py:906
#   WORKSPACE_MCP_PORT            auth/port_resolver.py:124
#   WORKSPACE_MCP_RESOLVED_PORT   auth/port_resolver.py:125  <- OAuthConfig READS this
#   MCP_ENABLE_OAUTH21            read back by OAuthConfig
#   WORKSPACE_MCP_STATELESS_MODE  read back by OAuthConfig
#
# Same defect class as the one fixed here: production code assigning into
# os.environ, which monkeypatch cannot restore because it did not set it.
KNOWN_UNFIXED_LEAKS = frozenset(
    {
        "OAUTHLIB_INSECURE_TRANSPORT",
        "OAUTHLIB_RELAX_TOKEN_SCOPE",
        "WORKSPACE_MCP_PORT",
        "WORKSPACE_MCP_RESOLVED_PORT",
        "MCP_ENABLE_OAUTH21",
        "WORKSPACE_MCP_STATELESS_MODE",
    }
)


def _is_pinned(name: str) -> bool:
    """Compare outside the assert, so a failure cannot print the value.

    `assert os.environ.get(name) == PINNED` is the obvious spelling and is
    unsafe: pytest's assertion rewriting renders both operands, and the value
    present when this guard fails is the real client secret it exists to
    contain. The moment of failure is also the moment someone pastes the output
    into an issue, so the naive form publishes the secret exactly when it stops
    being protected.
    """
    return os.environ.get(name) == PINNED


def _child_env() -> dict:
    """An environment in which NEITHER credential source can reach path 1.

    Stripping the variables is not enough: `auth/client_secrets.py` documents a
    repo-root client_secret.json as the default location, and README points
    users at it. A probe that ignored the file would fail on any ordinary
    developer checkout and blame the fixture -- reporting the property false
    where it was never establishable.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("FASTMCP_", "GOOGLE_OAUTH", "GOOGLE_CLIENT"))
    }
    env["GOOGLE_CLIENT_SECRET_PATH"] = str(REPO_ROOT / "does-not-exist-for-tests.json")
    return env


def _run_probe(body: str, tmp_path: Path, env: dict) -> tuple[int, dict]:
    """Run a probe script and return (inner pytest rc, parsed markers).

    Deliberately returns the inner rc: a session that died during collection
    leaves a clean environment for the trivial reason that nothing ran, which
    is indistinguishable from containment working.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "probe.py"
    script.write_text(body, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    markers = {}
    for line in completed.stdout.splitlines():
        if line.startswith("PROBE_"):
            key, _, value = line.partition(" ")
            markers[key] = value
    # Child output is NOT spliced into assertion messages: it is raw pytest
    # output from a session holding real credentials, and a failing auth test
    # inside it can render one.
    return completed.returncode, markers


@pytest.mark.parametrize("name", _FASTMCP_ENV_VARS)
def test_fastmcp_vars_are_pinned_before_any_test_body_runs(name):
    """The autouse fixture must reach every test, not only those that ask."""
    assert _is_pinned(name), f"{name} is not pinned (value withheld)"


def test_the_write_path_actually_runs_and_is_contained(monkeypatch):
    """Both halves: the write is ATTEMPTED, and it leaves nothing behind.

    The second half alone passes when `_apply_fastmcp_google_env` is deleted
    outright -- "contained" and "never ran" produce the identical observation.
    So this first clears one pinned name, proves the production code really
    does write it, and only then checks the pins are intact.
    """
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_ID", "leak-probe.apps.googleusercontent.com"
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "leak-probe-secret")

    probe_name = "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID"
    monkeypatch.delenv(probe_name, raising=False)

    config = OAuthConfig()

    assert config.client_id == "leak-probe.apps.googleusercontent.com"
    # POSITIVE CONTROL: without this, deleting the entire write path leaves
    # every other assertion in this file green.
    assert probe_name in os.environ, (
        f"{probe_name} was not written; the write path this fixture contains "
        "did not run, so the containment assertions below prove nothing"
    )

    # monkeypatch restores the pin for probe_name at teardown; the rest must be
    # untouched right now.
    for name in _FASTMCP_ENV_VARS:
        if name == probe_name:
            continue
        assert _is_pinned(name), (
            f"{name} was overwritten by OAuthConfig (value withheld); without "
            "the pin it would outlive this test and configure every later one"
        )


def test_the_pinned_list_covers_everything_the_write_path_sets():
    """Derive the expected names from the SOURCE, not from the same tuple.

    Without this the suite is self-referential: the fixture pins
    ``_FASTMCP_ENV_VARS`` and the tests parametrise over ``_FASTMCP_ENV_VARS``,
    so deleting a name removes the pin AND its assertion together, and the
    suite stays green while that value leaks.

    The count is pinned, not just non-emptiness: a partial drift -- a comment
    after the open paren, one name moved into a loop -- would otherwise shrink
    the scraped set silently while the bare "found something" control passed.
    """
    source = (REPO_ROOT / "auth" / "oauth_config.py").read_text(encoding="utf-8")
    written = set(re.findall(r'_set_if_absent\(\s*"([A-Z0-9_]+)"', source))

    assert len(written) == 5, (
        f"expected 5 _set_if_absent literals in auth/oauth_config.py, found "
        f"{len(written)}: {sorted(written)}. Either the write path changed and "
        "this list needs updating, or the regex no longer matches how those "
        "calls are written -- and a silently-shrinking set is how a new name "
        "leaks past every control here."
    )

    missing = sorted(written - set(_FASTMCP_ENV_VARS))
    assert not missing, (
        "auth/oauth_config.py writes these, but tests/conftest.py does not pin "
        f"them, so they will leak: {missing}"
    )


def test_the_pin_survives_a_peer_fixture_undoing_the_shared_monkeypatch(tmp_path):
    """The pin must not share a MonkeyPatch with fixtures that call undo().

    tests/auth/conftest.py's ``isolated_gateway_config`` calls
    ``monkeypatch.undo()`` in teardown and then rebuilds the config singleton.
    Sharing one MonkeyPatch means that ``undo()`` erases the pin FIRST, and the
    rebuild writes the real credentials back -- after every in-body assertion
    has already passed.

    Pinned separately because the session probe below cannot see this: it runs
    without credentials, where the rebuild returns early and the bug cannot
    occur.
    """
    # The two fixtures MUST sit in a parent/child conftest pair, as they do in
    # the repository. In ONE conftest pytest registers autouse fixtures
    # alphabetically (FixtureManager.parsefactories iterates `dir(holderobj)`),
    # so "peer_" would tear down after "pinned_" -- the reverse of reality, and
    # the test would fail for a reason unrelated to the bug.
    (tmp_path / "conftest.py").write_text(
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from tests.conftest import pinned_fastmcp_env  # noqa: F401  (real one)\n",
        encoding="utf-8",
    )
    child = tmp_path / "child"
    child.mkdir()
    (child / "conftest.py").write_text(
        "import pytest\n"
        "from auth import oauth_config\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def peer_that_undoes_the_shared_monkeypatch(monkeypatch):\n"
        "    yield\n"
        "    monkeypatch.undo()\n"
        "    oauth_config.reload_oauth_config()\n",
        encoding="utf-8",
    )
    (child / "test_builds_a_config.py").write_text(
        "from auth.oauth_config import OAuthConfig\n"
        "\n"
        "def test_build():\n"
        "    assert OAuthConfig().client_id\n",
        encoding="utf-8",
    )

    env = _child_env()
    # A credential IS required here: without one the rebuild returns early and
    # the teardown bug this test exists for cannot happen.
    env["GOOGLE_OAUTH_CLIENT_ID"] = "teardown-probe.apps.googleusercontent.com"
    env["GOOGLE_OAUTH_CLIENT_SECRET"] = "teardown-probe-secret"

    _rc, markers = _run_probe(
        f"import os, sys, pytest\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"rc = pytest.main(['-q', '--no-header', '-p', 'no:cacheprovider', "
        f"{str(tmp_path)!r}])\n"
        'print("PROBE_RC", int(rc))\n'
        'print("PROBE_LEFT", ",".join(sorted('
        '    k for k in os.environ if k.startswith("FASTMCP_SERVER_AUTH"))))\n',
        tmp_path / "runner",
        env,
    )

    assert "PROBE_RC" in markers and "PROBE_LEFT" in markers, (
        "probe produced no result markers; the measurement failed and must not "
        "be read as a clean environment"
    )
    assert markers["PROBE_RC"] == "0", (
        f"inner session failed (rc={markers['PROBE_RC']}); a clean environment "
        "from a session that did not run proves nothing"
    )
    assert markers["PROBE_LEFT"] == "", (
        "a peer fixture's monkeypatch.undo() released the pin and the rebuild "
        f"wrote credentials back: {markers['PROBE_LEFT']}"
    )


def test_a_session_adds_no_unexpected_environment_variables(tmp_path):
    """Diff the WHOLE environment, not just names matching a known prefix.

    A prefix filter can only see leaks of names already enumerated, which is
    the same self-reference the source-scrape above exists to break -- and it
    is blind to precisely the case that matters: a write under a name nobody
    has listed yet. Snapshotting the entire environment costs nothing extra and
    turns every unfixed leak into a named, reviewable exception.

    Targets tests/core/test_http_oauth_scope_config.py: it builds a configured
    OAuthConfig outside tests/auth/, so it witnesses both the containment and
    the fixture's PLACEMENT (a fixture demoted into tests/auth/conftest.py
    would stop covering it), at a fraction of the cost of sweeping two
    directories.
    """
    # One test node, not the whole file: it is the witness that matters and the
    # file's other tests add ~15s of server setup this probe does not need.
    target = (
        "tests/core/test_http_oauth_scope_config.py"
        "::test_configure_server_for_http_accepts_client_secret_from_file"
    )
    _rc, markers = _run_probe(
        "import os, sys, pytest\n"
        f"TARGET = {target!r}\n"
        "before = dict(os.environ)\n"
        "rc = pytest.main(['-q', '--no-header', '-p', 'no:cacheprovider',\n"
        "                  TARGET])\n"
        "after = dict(os.environ)\n"
        'print("PROBE_RC", int(rc))\n'
        'print("PROBE_ADDED", ",".join(sorted(set(after) - set(before))))\n'
        'print("PROBE_CHANGED", ",".join(sorted(\n'
        "    k for k in set(before) & set(after) if before[k] != after[k])))\n",
        tmp_path,
        _child_env(),
    )

    assert {"PROBE_RC", "PROBE_ADDED", "PROBE_CHANGED"} <= markers.keys(), (
        "probe produced no result markers; the measurement failed and must not "
        "be read as a clean environment"
    )
    assert markers["PROBE_RC"] == "0", (
        f"inner session failed (rc={markers['PROBE_RC']}); a clean environment "
        "from a session that did not run proves nothing"
    )

    added = {name for name in markers["PROBE_ADDED"].split(",") if name}
    unexpected = sorted(added - KNOWN_UNFIXED_LEAKS)
    assert not unexpected, (
        "the session added environment variables that nothing accounts for: "
        f"{unexpected}. If one is a new leak, contain it; if it is expected, "
        "add it to KNOWN_UNFIXED_LEAKS with its writer's file:line."
    )

    changed = {name for name in markers["PROBE_CHANGED"].split(",") if name}
    assert not changed, (
        f"the session changed the value of existing variables: {sorted(changed)}"
    )
