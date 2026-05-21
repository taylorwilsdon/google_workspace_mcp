import subprocess
import sys


def test_core_server_import_suppresses_fastmcp_authlib_jose_warning():
    result = subprocess.run(
        [sys.executable, "-c", "import core.server"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "AuthlibDeprecationWarning" not in result.stderr
    assert "authlib.jose module is deprecated" not in result.stderr
