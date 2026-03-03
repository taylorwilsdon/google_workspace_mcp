from gmail.gmail_tools import _format_body_content


def test_format_body_content_uses_html_when_plain_text_missing():
    result = _format_body_content("", "<p>Hello <b>world</b></p>")

    assert result == "Hello world"


def test_format_body_content_uses_html_when_plain_text_is_footer_only():
    plain_footer = """_______________________________________________
Pilotes mailing list
Pilotes@lesmartinets.org
http://lists.lesmartinets.org/mailman/listinfo/pilotes"""
    html_body = """
<div>Salut a vous toutes et tous.</div>
<div>Les beaux jours reviennent, il est temps de preparer nos oiseaux.</div>
<hr/>
<div>_______________________________________________</div>
<div>Pilotes mailing list</div>
<div>Pilotes@lesmartinets.org</div>
<div>http://lists.lesmartinets.org/mailman/listinfo/pilotes</div>
"""

    result = _format_body_content(plain_footer, html_body)

    assert "Salut a vous toutes et tous." in result
    assert "Les beaux jours reviennent" in result


def test_format_body_content_keeps_meaningful_plain_text():
    plain_body = "Team, please review by EOD.\nThanks."
    html_body = "<p>Team, please review by EOD.</p><p>Thanks.</p>"

    result = _format_body_content(plain_body, html_body)

    assert result == plain_body


def test_format_body_content_uses_html_for_known_plain_placeholder():
    plain_placeholder = "Your client does not support HTML messages."
    html_body = "<p>Actual message body for recipients.</p>"

    result = _format_body_content(plain_placeholder, html_body)

    assert result == "Actual message body for recipients."
