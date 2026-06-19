"""Tests for XSS prevention in OAuth callback HTML responses."""

from auth.oauth_responses import (
    create_error_response,
    create_server_error_response,
    create_success_response,
)


class TestXSSPrevention:
    def test_error_response_escapes_script_tag(self):
        xss_payload = '<script>alert("xss")</script>'
        response = create_error_response(xss_payload)
        body = response.body.decode()
        assert "<script>alert" not in body
        assert "&lt;script&gt;" in body

    def test_error_response_escapes_html_entities(self):
        response = create_error_response('Test <b>bold</b> & "quotes"')
        body = response.body.decode()
        assert "<b>" not in body
        assert "&lt;b&gt;" in body
        assert "&amp;" in body

    def test_success_response_escapes_user_display(self):
        xss_email = "<img src=x onerror=alert(1)>@evil.com"
        response = create_success_response(verified_user_id=xss_email)
        body = response.body.decode()
        # The raw <img> tag should not appear — only the escaped version
        assert "<img src=" not in body
        assert "&lt;img src=x onerror=alert(1)&gt;@evil.com" in body

    def test_success_response_normal_email_displays_correctly(self):
        response = create_success_response(verified_user_id="user@example.com")
        body = response.body.decode()
        assert "user@example.com" in body

    def test_success_response_none_user_shows_default(self):
        response = create_success_response(verified_user_id=None)
        body = response.body.decode()
        assert "Google User" in body

    def test_server_error_response_escapes_exception(self):
        xss_detail = "FileNotFoundError: /secret/path/<script>alert(1)</script>"
        response = create_server_error_response(xss_detail)
        body = response.body.decode()
        assert "<script>alert" not in body
        assert "&lt;script&gt;" in body

    def test_error_response_status_code(self):
        response = create_error_response("test", status_code=403)
        assert response.status_code == 403

    def test_server_error_response_status_code(self):
        response = create_server_error_response("test")
        assert response.status_code == 500

    def test_success_response_status_code(self):
        response = create_success_response("user@example.com")
        assert response.status_code == 200
