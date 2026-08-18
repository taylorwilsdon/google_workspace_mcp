import pytest

import auth.service_decorator as service_decorator


class _FakeService:
    def __init__(self, name: str, events: list[str]):
        self.name = name
        self._events = events

    def close(self) -> None:
        self._events.append(f"close:{self.name}")


def _patch_common_decorator_state(monkeypatch):
    async def fake_get_auth_context(tool_name):
        return (None, None, None)

    monkeypatch.setattr(service_decorator, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(service_decorator, "_get_auth_context", fake_get_auth_context)
    monkeypatch.setattr(
        service_decorator,
        "_extract_oauth20_user_email",
        lambda args, kwargs, wrapper_sig: "user@example.com",
    )
    monkeypatch.setattr(
        service_decorator,
        "_override_oauth21_user_email",
        lambda use_oauth21, authenticated_user, user_google_email, args, kwargs, wrapper_params, tool_name, service_type=None: (
            user_google_email,
            args,
        ),
    )
    monkeypatch.setattr(
        service_decorator, "_detect_oauth_version", lambda *args, **kwargs: False
    )


@pytest.mark.asyncio
async def test_require_google_service_releases_cycles_after_close(monkeypatch):
    _patch_common_decorator_state(monkeypatch)
    events = []
    fake_service = _FakeService("gmail", events)

    async def fake_authenticate_service(*args, **kwargs):
        return fake_service, "user@example.com"

    monkeypatch.setattr(
        service_decorator, "_authenticate_service", fake_authenticate_service
    )
    monkeypatch.setattr(
        service_decorator,
        "_release_google_service_cycles",
        lambda: events.append("collect"),
    )

    @service_decorator.require_google_service("gmail", "gmail_read")
    async def sample_tool(service, user_google_email: str):
        assert service is fake_service
        assert user_google_email == "user@example.com"
        events.append("func")
        return "ok"

    result = await sample_tool(user_google_email="user@example.com")

    assert result == "ok"
    assert events == ["func", "close:gmail", "collect"]


@pytest.mark.asyncio
async def test_require_multiple_services_releases_cycles_after_exit_stack(
    monkeypatch,
):
    _patch_common_decorator_state(monkeypatch)
    events = []
    services = {
        "drive": _FakeService("drive", events),
        "docs": _FakeService("docs", events),
    }

    async def fake_authenticate_service(
        use_oauth21,
        service_name,
        service_version,
        tool_name,
        user_google_email,
        resolved_scopes,
        mcp_session_id,
        authenticated_user,
    ):
        return services[service_name], user_google_email

    monkeypatch.setattr(
        service_decorator, "_authenticate_service", fake_authenticate_service
    )
    monkeypatch.setattr(
        service_decorator,
        "_release_google_service_cycles",
        lambda: events.append("collect"),
    )

    @service_decorator.require_multiple_services(
        [
            {
                "service_type": "drive",
                "scopes": "drive_read",
                "param_name": "drive_service",
            },
            {
                "service_type": "docs",
                "scopes": "docs_read",
                "param_name": "docs_service",
            },
        ]
    )
    async def sample_tool(drive_service, docs_service, user_google_email: str):
        assert drive_service is services["drive"]
        assert docs_service is services["docs"]
        assert user_google_email == "user@example.com"
        events.append("func")
        return "ok"

    result = await sample_tool(user_google_email="user@example.com")

    assert result == "ok"
    assert events == ["func", "close:docs", "close:drive", "collect"]


@pytest.mark.asyncio
async def test_require_multiple_services_collects_after_partial_auth_failure(
    monkeypatch,
):
    _patch_common_decorator_state(monkeypatch)
    events = []
    drive_service = _FakeService("drive", events)

    async def fake_authenticate_service(
        use_oauth21,
        service_name,
        service_version,
        tool_name,
        user_google_email,
        resolved_scopes,
        mcp_session_id,
        authenticated_user,
    ):
        if service_name == "drive":
            return drive_service, user_google_email
        raise service_decorator.GoogleAuthenticationError("docs auth failed")

    monkeypatch.setattr(
        service_decorator, "_authenticate_service", fake_authenticate_service
    )
    monkeypatch.setattr(
        service_decorator,
        "_release_google_service_cycles",
        lambda: events.append("collect"),
    )

    @service_decorator.require_multiple_services(
        [
            {
                "service_type": "drive",
                "scopes": "drive_read",
                "param_name": "drive_service",
            },
            {
                "service_type": "docs",
                "scopes": "docs_read",
                "param_name": "docs_service",
            },
        ]
    )
    async def sample_tool(drive_service, docs_service, user_google_email: str):
        raise AssertionError("tool body should not run when auth fails")

    with pytest.raises(
        service_decorator.GoogleAuthenticationError, match="docs auth failed"
    ):
        await sample_tool(user_google_email="user@example.com")

    assert events == ["close:drive", "collect"]


@pytest.mark.asyncio
async def test_require_multiple_services_optional_failure_injects_none(monkeypatch):
    """An optional service that fails to authenticate is injected as None instead
    of failing the whole tool, so the primary action still runs (graceful
    degradation). The required service is unaffected."""
    _patch_common_decorator_state(monkeypatch)
    events = []
    gmail_service = _FakeService("gmail", events)

    async def fake_authenticate_service(
        use_oauth21,
        service_name,
        service_version,
        tool_name,
        user_google_email,
        resolved_scopes,
        mcp_session_id,
        authenticated_user,
    ):
        if service_name == "gmail":
            return gmail_service, user_google_email
        # The optional People service lacks the contacts scope.
        raise service_decorator.GoogleAuthenticationError("contacts scope missing")

    monkeypatch.setattr(
        service_decorator, "_authenticate_service", fake_authenticate_service
    )
    monkeypatch.setattr(
        service_decorator,
        "_release_google_service_cycles",
        lambda: events.append("collect"),
    )

    @service_decorator.require_multiple_services(
        [
            {
                "service_type": "gmail",
                "scopes": "gmail_read",
                "param_name": "service",
            },
            {
                "service_type": "people",
                "scopes": "contacts_read",
                "param_name": "people_service",
                "optional": True,
            },
        ]
    )
    async def sample_tool(service, people_service, user_google_email: str):
        assert service is gmail_service
        assert people_service is None  # degraded gracefully
        events.append("func")
        return "ran"

    result = await sample_tool(user_google_email="user@example.com")

    assert result == "ran"
    # Tool ran; only the gmail service was created/closed.
    assert events == ["func", "close:gmail", "collect"]


@pytest.mark.asyncio
async def test_require_multiple_services_optional_non_auth_error_reraises(monkeypatch):
    """An optional service that fails with a NON-auth error must NOT be swallowed
    -- only authentication failures degrade gracefully. Real bugs surface."""
    _patch_common_decorator_state(monkeypatch)
    events = []
    gmail_service = _FakeService("gmail", events)

    async def fake_authenticate_service(
        use_oauth21,
        service_name,
        service_version,
        tool_name,
        user_google_email,
        resolved_scopes,
        mcp_session_id,
        authenticated_user,
    ):
        if service_name == "gmail":
            return gmail_service, user_google_email
        # A non-auth failure (e.g. a bug building the optional service).
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(
        service_decorator, "_authenticate_service", fake_authenticate_service
    )
    monkeypatch.setattr(
        service_decorator,
        "_release_google_service_cycles",
        lambda: events.append("collect"),
    )

    @service_decorator.require_multiple_services(
        [
            {"service_type": "gmail", "scopes": "gmail_read", "param_name": "service"},
            {
                "service_type": "people",
                "scopes": "contacts_read",
                "param_name": "people_service",
                "optional": True,
            },
        ]
    )
    async def sample_tool(service, people_service, user_google_email: str):
        events.append("func")
        return "ran"

    with pytest.raises(RuntimeError, match="unexpected boom"):
        await sample_tool(user_google_email="user@example.com")

    # The tool body never ran -- the non-auth error propagated.
    assert "func" not in events
    # The already-opened Gmail service is still cleaned up on the re-raise path
    # (no leak when an optional service fails non-auth).
    assert events == ["close:gmail", "collect"]
