from core import config


def test_default_is_api(monkeypatch):
    monkeypatch.delenv("GMAIL_SEND_TRANSPORT", raising=False)
    assert config.get_send_transport() == "api"


def test_smtp_opt_in(monkeypatch):
    monkeypatch.setenv("GMAIL_SEND_TRANSPORT", "smtp")
    assert config.get_send_transport() == "smtp"


def test_unknown_falls_back_to_api(monkeypatch, caplog):
    monkeypatch.setenv("GMAIL_SEND_TRANSPORT", "bogus")
    assert config.get_send_transport() == "api"


def test_whitespace_and_case_normalization(monkeypatch):
    """Verify that whitespace and case variants normalize to 'smtp'."""
    monkeypatch.setenv("GMAIL_SEND_TRANSPORT", " SMTP ")
    assert config.get_send_transport() == "smtp"

    monkeypatch.setenv("GMAIL_SEND_TRANSPORT", "Smtp")
    assert config.get_send_transport() == "smtp"

    monkeypatch.setenv("GMAIL_SEND_TRANSPORT", " SmTp ")
    assert config.get_send_transport() == "smtp"
