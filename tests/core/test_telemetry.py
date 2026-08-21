"""Tests for the opt-in OpenTelemetry bootstrap."""

import builtins
import logging
from types import SimpleNamespace

import pytest

from core import telemetry


@pytest.fixture(autouse=True)
def _clear_otel_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_SERVICE_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_configure_telemetry_is_noop_without_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_exporter():
        raise AssertionError("exporter must not be built without an endpoint")

    monkeypatch.setattr(telemetry, "_build_span_exporter", unexpected_exporter)

    assert telemetry.configure_telemetry() is False


@pytest.mark.parametrize(
    "name",
    ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
)
def test_either_standard_endpoint_enables_configuration(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "http://collector:4317")
    monkeypatch.setattr(telemetry, "_build_span_exporter", lambda: (object(), "grpc"))

    from opentelemetry import trace
    from opentelemetry.sdk import trace as sdk_trace
    from opentelemetry.sdk.trace import export as trace_export

    configured: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, *, resource) -> None:
            configured["resource"] = resource

        def add_span_processor(self, processor) -> None:
            configured["processor"] = processor

    class FakeProcessor:
        def __init__(self, exporter) -> None:
            configured["exporter"] = exporter

    monkeypatch.setattr(
        trace, "get_tracer_provider", lambda: trace.ProxyTracerProvider()
    )
    monkeypatch.setattr(
        trace,
        "set_tracer_provider",
        lambda provider: configured.update(provider=provider),
    )
    monkeypatch.setattr(sdk_trace, "TracerProvider", FakeProvider)
    monkeypatch.setattr(trace_export, "BatchSpanProcessor", FakeProcessor)

    assert telemetry.configure_telemetry() is True
    assert configured["resource"].attributes["service.name"] == (
        telemetry.DEFAULT_SERVICE_NAME
    )
    assert "provider" in configured
    assert "processor" in configured
    assert "exporter" in configured


def test_missing_sdk_logs_hint_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    real_import = builtins.__import__

    def import_without_sdk(name, *args, **kwargs):
        if name.startswith("opentelemetry.sdk"):
            raise ImportError("simulated missing optional dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_sdk)

    with caplog.at_level(logging.WARNING, logger=telemetry.__name__):
        assert telemetry.configure_telemetry() is False

    assert len(caplog.records) == 1
    assert "'otel' extra" in caplog.text


def test_existing_provider_is_left_untouched(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")

    from opentelemetry import trace

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: object())

    def unexpected_exporter():
        raise AssertionError("exporter must not be built for an existing provider")

    monkeypatch.setattr(telemetry, "_build_span_exporter", unexpected_exporter)

    with caplog.at_level(logging.INFO, logger=telemetry.__name__):
        assert telemetry.configure_telemetry() is False

    assert "already configured" in caplog.text


def test_resource_attributes_can_set_service_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "service.name=custom-service,deployment.environment.name=test",
    )

    resource = telemetry._build_resource()

    assert resource.attributes["service.name"] == "custom-service"
    assert resource.attributes["deployment.environment.name"] == "test"


def test_default_service_name_is_used_when_otel_does_not_supply_one() -> None:
    resource = telemetry._build_resource()

    assert resource.attributes["service.name"] == telemetry.DEFAULT_SERVICE_NAME


@pytest.mark.parametrize(
    ("protocol", "module_fragment"),
    (("grpc", ".grpc."), ("http/protobuf", ".http.")),
)
def test_build_span_exporter_selects_protocol(
    monkeypatch: pytest.MonkeyPatch, protocol: str, module_fragment: str
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
    imported: list[str] = []
    real_import = builtins.__import__

    class FakeExporter:
        pass

    def import_with_fake_exporter(name, *args, **kwargs):
        if "opentelemetry.exporter.otlp.proto" in name:
            imported.append(name)
            return SimpleNamespace(OTLPSpanExporter=FakeExporter)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_fake_exporter)

    exporter, selected_protocol = telemetry._build_span_exporter()

    assert isinstance(exporter, FakeExporter)
    assert selected_protocol == protocol
    assert module_fragment in imported[0]


def test_trace_protocol_overrides_general_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    imported: list[str] = []
    real_import = builtins.__import__

    def import_with_fake_exporter(name, *args, **kwargs):
        if "opentelemetry.exporter.otlp.proto" in name:
            imported.append(name)
            return SimpleNamespace(OTLPSpanExporter=object)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_fake_exporter)

    _, selected_protocol = telemetry._build_span_exporter()

    assert selected_protocol == "http/protobuf"
    assert ".http." in imported[0]


def test_invalid_protocol_returns_false_without_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/json")

    from opentelemetry import trace

    monkeypatch.setattr(
        trace, "get_tracer_provider", lambda: trace.ProxyTracerProvider()
    )

    with caplog.at_level(logging.WARNING, logger=telemetry.__name__):
        assert telemetry.configure_telemetry() is False

    assert "must be 'grpc' or 'http/protobuf'" in caplog.text


def test_exporter_configuration_error_never_prevents_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")

    from opentelemetry import trace

    monkeypatch.setattr(
        trace, "get_tracer_provider", lambda: trace.ProxyTracerProvider()
    )

    def broken_exporter():
        raise OSError("certificate file is unreadable")

    monkeypatch.setattr(telemetry, "_build_span_exporter", broken_exporter)

    with caplog.at_level(logging.WARNING, logger=telemetry.__name__):
        assert telemetry.configure_telemetry() is False

    assert "certificate file is unreadable" in caplog.text


class _FakeSpan:
    def __init__(self, recording: bool = True) -> None:
        self._recording = recording
        self.attributes: dict[str, object] = {}

    def is_recording(self) -> bool:
        return self._recording

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


def _patch_current_span(monkeypatch: pytest.MonkeyPatch, span: _FakeSpan) -> None:
    from opentelemetry import trace

    monkeypatch.setattr(trace, "get_current_span", lambda: span)


def test_record_authenticated_user_sets_email_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(telemetry.USER_EMAIL_ATTRIBUTE_ENV, "true")
    span = _FakeSpan()
    _patch_current_span(monkeypatch, span)

    telemetry.record_authenticated_user("alice@example.com")

    assert span.attributes == {"user.email": "alice@example.com"}


@pytest.mark.parametrize("value", ("1", "yes", "on", "TRUE"))
def test_record_authenticated_user_accepts_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(telemetry.USER_EMAIL_ATTRIBUTE_ENV, value)
    span = _FakeSpan()
    _patch_current_span(monkeypatch, span)

    telemetry.record_authenticated_user("bob@example.com")

    assert span.attributes == {"user.email": "bob@example.com"}


def test_record_authenticated_user_is_noop_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(telemetry.USER_EMAIL_ATTRIBUTE_ENV, raising=False)
    span = _FakeSpan()
    _patch_current_span(monkeypatch, span)

    telemetry.record_authenticated_user("alice@example.com")

    assert span.attributes == {}


@pytest.mark.parametrize("value", ("", "false", "0", "no"))
def test_record_authenticated_user_ignores_falsy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(telemetry.USER_EMAIL_ATTRIBUTE_ENV, value)
    span = _FakeSpan()
    _patch_current_span(monkeypatch, span)

    telemetry.record_authenticated_user("alice@example.com")

    assert span.attributes == {}


@pytest.mark.parametrize("email", (None, ""))
def test_record_authenticated_user_skips_empty_email(
    monkeypatch: pytest.MonkeyPatch, email: object
) -> None:
    monkeypatch.setenv(telemetry.USER_EMAIL_ATTRIBUTE_ENV, "true")
    span = _FakeSpan()
    _patch_current_span(monkeypatch, span)

    telemetry.record_authenticated_user(email)  # type: ignore[arg-type]

    assert span.attributes == {}


def test_record_authenticated_user_skips_non_recording_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(telemetry.USER_EMAIL_ATTRIBUTE_ENV, "true")
    span = _FakeSpan(recording=False)
    _patch_current_span(monkeypatch, span)

    telemetry.record_authenticated_user("alice@example.com")

    assert span.attributes == {}


def test_record_authenticated_user_never_raises_without_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(telemetry.USER_EMAIL_ATTRIBUTE_ENV, "true")
    real_import = builtins.__import__

    def import_without_otel(name, *args, **kwargs):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("simulated missing optional dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_otel)

    telemetry.record_authenticated_user("alice@example.com")  # must not raise
