"""A quoted or forwarded HTML document is nested as a fragment.

Desktop mail clients send their HTML part as a whole document. Quoted or
forwarded unchanged, its ``<html>``, ``<head>`` and ``<body>`` would end up
inside the new message's own HTML fragment.

All Gmail API calls are mocked. Synthetic data only (example.com, generic names).
"""

import base64
import re
import time
from email import message_from_bytes
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import pytest

from gmail import gmail_quoted_html
from gmail.gmail_helpers import _build_forward_content, _build_web_reply_bodies
from gmail.gmail_quoted_html import normalize_quoted_html
from gmail.gmail_tools import (
    _build_quoted_reply_body,
    draft_gmail_message,
    send_gmail_message,
)

# The shape Outlook sends: no doctype, a Word style sheet, conditional comments.
OUTLOOK_DOCUMENT = """<html xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta name="Generator" content="Microsoft Word 15 (filtered medium)">
<title>Quarterly figures</title>
<style><!--
/* Font Definitions */
@font-face
\t{font-family:Aptos;
\tpanose-1:2 11 0 4 2 2 2 2 2 4;}
p.MsoNormal, li.MsoNormal, div.MsoNormal
\t{margin:0cm;
\tfont-size:12.0pt;
\tfont-family:"Aptos",sans-serif;
\tmso-ligatures:standardcontextual;}
a:link, span.MsoHyperlink
\t{mso-style-priority:99;
\tcolor:#467886;
\ttext-decoration:underline;}
@page WordSection1
\t{size:612.0pt 792.0pt;}
div.WordSection1
\t{page:WordSection1;}
--></style><!--[if gte mso 9]><xml>
<o:shapedefaults v:ext="edit" spidmax="1026" />
</xml><![endif]-->
</head>
<body lang="EN-GB" link="#467886" vlink="#96607D" style="word-wrap:break-word">
<div class="WordSection1">
<p class="MsoNormal">Dear Grace,<o:p></o:p></p>
<p class="MsoNormal"><o:p>&nbsp;</o:p></p>
<p class="MsoNormal">The AT&T offer &amp; the <a href="https://example.com/?a=1&amp;b=2">link</a> &lt;here&gt;.<o:p></o:p></p>
<table border="0"><tr><td><p class="MsoNormal" style="color:red">Cell<o:p></o:p></p></td></tr></table>
<p class="MsoNormal"><![if !supportLists]><span>&middot;</span><![endif]>Bullet<o:p></o:p></p>
<p class="MsoNormal"><a name="_MailOriginal"><b>From:</b></a> Ada Example</p>
<blockquote style="margin:0"><p class="MsoNormal">Older quote</p></blockquote>
</div>
</body>
</html>
"""
GMAIL_FRAGMENT = (
    '<div dir="ltr">Thanks &amp; regards<br><div class="gmail_quote">older</div></div>'
)
DOCUMENT_MARKUP = re.compile(
    r"<(?:html|head|body|meta|title|style|base|link|script|xml)\b|<!doctype|<!--",
    re.IGNORECASE,
)
MSO_NORMAL = (
    'style="margin:0cm;font-size:12.0pt;font-family:&quot;Aptos&quot;,sans-serif"'
)
REPLY = "The figures are attached."
PARENT = {
    "from": "Ada Example <ada@example.com>",
    "date": "Tue, 10 Nov 2026 14:05:00 +0100",
}
ATTACHMENT = [
    {
        "filename": "figures.txt",
        "mime_type": "text/plain",
        "content": base64.b64encode(b"1,2,3\n").decode(),
    }
]


def test_fragment_is_returned_unchanged() -> None:
    assert normalize_quoted_html(GMAIL_FRAGMENT) is GMAIL_FRAGMENT
    assert normalize_quoted_html("") == ""
    assert normalize_quoted_html("only text") == "only text"


def test_outlook_document_becomes_a_fragment() -> None:
    out = normalize_quoted_html(OUTLOOK_DOCUMENT)

    assert not DOCUMENT_MARKUP.search(out), out
    # The body's lang and style move to a div; its link colours do not.
    assert out.startswith('<div lang="EN-GB" style="word-wrap:break-word">')
    assert out.endswith("</div>")
    # The style sheet is inlined, without the Office-only declarations.
    assert f'<p class="MsoNormal" {MSO_NORMAL}>Dear Grace,<o:p></o:p></p>' in out
    assert "mso-" not in out
    assert 'Aptos&quot;,sans-serif;color:red">Cell' in out
    # ":link" styles a link, never a named anchor.
    assert 'b=2" style="color:#467886;text-decoration:underline">link</a>' in out
    assert '<a name="_MailOriginal"><b>From:</b></a>' in out
    # Text is copied as written, and so is what the conditional markers enclose.
    assert "The AT&T offer &amp; the <a" in out
    assert "&lt;here&gt;.<o:p></o:p>" in out
    assert "<span>&middot;</span>Bullet" in out
    assert "supportLists" not in out
    assert "Quarterly figures" not in out
    assert '<blockquote style="margin:0"><p class="MsoNormal"' in out


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<html><head><meta http-equiv="content-type" content="text/html; '
            'charset=utf-8"></head><body style="overflow-wrap: break-word;">'
            "<div>Hello!</div><div><br></div></body></html>",
            '<div style="overflow-wrap: break-word;"><div>Hello!</div>'
            "<div><br></div></div>",
            id="apple-mail",
        ),
        pytest.param(
            '<!DOCTYPE html>\n<html>\n  <head>\n    <meta charset="UTF-8">\n'
            "  </head>\n  <body>\n    <p>Hello,</p>\n  </body>\n</html>\n",
            "<p>Hello,</p>",
            id="thunderbird",
        ),
        pytest.param(
            '<HTML><HEAD><TITLE>T</TITLE></HEAD><BODY DIR="RTL" BGCOLOR="#FFFFFF">'
            "<P ALIGN=center>Hi<BR>there</P></BODY></HTML>",
            '<div DIR="RTL"><P ALIGN=center>Hi<BR>there</P></div>',
            id="upper-case",
        ),
        pytest.param(
            "<html><head><title>T</title><body><p>hi</p><script>x()</script>"
            "<!-- note --></body>",
            "<p>hi</p>",
            id="unclosed-head-script-comment",
        ),
        pytest.param(
            '<html><body lang="en"><p>in</p></body></html>\n<p>list footer</p>',
            '<div lang="en"><p>in</p>\n<p>list footer</p></div>',
            id="content-after-the-document",
        ),
        pytest.param(
            '<div dir="ltr">Reply<blockquote><html><head><title>T</title></head>'
            '<body lang="de"><p>Original</p></body></html></blockquote></div>',
            '<div lang="de"><div dir="ltr">Reply<blockquote><p>Original</p>'
            "</blockquote></div></div>",
            id="document-nested-in-a-fragment",
        ),
        pytest.param(
            "<div>a<!--[if mso]><p>Outlook only</p><![endif]-->b</div>",
            "<div>ab</div>",
            id="conditional-comment-in-a-fragment",
        ),
        pytest.param(
            '<meta name="x" content="y"><link rel="stylesheet" href="x.css">'
            '<base href="https://example.com/"><a href="page.html">relative</a>',
            '<a href="page.html">relative</a>',
            id="head-only-tags-in-a-fragment",
        ),
    ],
)
def test_document_markup_is_dropped(source: str, expected: str) -> None:
    assert normalize_quoted_html(source) == expected


def test_kept_markup_is_copied_as_written() -> None:
    """No attribute is re-serialised: quoting, case and entities stay as sent."""
    body = (
        '<p CLASS="a" hidden data-x=\'say "hi"\' title="it&apos;s">'
        '<a href="https://example.com/?a=1&currency=EUR&timestamp=5">pay</a> '
        "AT&T &copy 2026 &nbsp;&#8364;5 a < b</p>"
        '<svg viewBox="0 0 9 9"><path class="a" d="M0 0"/></svg><br/>'
    )
    assert normalize_quoted_html(f"<html><body>{body}</body></html>") == body

    styled = normalize_quoted_html(
        "<html><head><style>a{color:red} .a{fill:blue}</style></head>"
        f"<body>{body}</body></html>"
    )
    assert (
        '<a href="https://example.com/?a=1&currency=EUR&timestamp=5" '
        'style="color:red">pay</a>'
    ) in styled
    assert (
        '<p CLASS="a" hidden data-x=\'say "hi"\' title="it&apos;s" style="fill:blue">'
    ) in styled
    # A self-closing tag stays self-closing, or the next element nests in it.
    assert '<path class="a" d="M0 0" style="fill:blue"/></svg>' in styled


@pytest.mark.parametrize(
    ("sheet", "element", "expected"),
    [
        pytest.param(
            "p{color:blue} .a{color:red} p.a{margin:0}",
            '<p class="a" style="color:black">',
            '<p class="a" style="color:blue;color:red;margin:0;color:black">',
            id="cascade-order-own-style-last",
        ),
        pytest.param(
            "p{color:blue !important} .a{color:red}",
            '<p class="a">',
            '<p class="a" style="color:blue !important;color:red">',
            id="important-is-left-to-the-reader",
        ),
        pytest.param(
            '.a{background:url(data:image/png;base64,AAAA) no-repeat;font-family:"x;y"}',
            "<p class=a style='font-family:\"Aptos\"'>",
            "<p class=a style='background:url(data:image/png;base64,AAAA) "
            'no-repeat;font-family:&quot;x;y&quot;;font-family:"Aptos"\'>',
            id="semicolons-in-urls-and-strings",
        ),
        pytest.param(
            'p{font-family:"unterminated\n;color:red} .a{color:blue}',
            '<p class="a">',
            '<p class="a" style="color:red;color:blue">',
            id="unterminated-string-drops-its-declaration",
        ),
        pytest.param(
            "@media (max-width:480px){.a{display:none}} @font-face{font-family:x}"
            " div .a{color:red} .a:hover{color:red} #a{color:red} .a{color:blue}",
            '<p class="a" id="a">',
            '<p class="a" id="a" style="color:blue">',
            id="at-rules-and-other-selectors-are-skipped",
        ),
        pytest.param(
            "a:link{color:red}",
            '<a name="bookmark">',
            '<a name="bookmark">',
            id="link-rule-skips-a-named-anchor",
        ),
    ],
)
def test_style_sheet_rules_are_inlined(sheet: str, element: str, expected: str) -> None:
    out = normalize_quoted_html(
        f"<html><head><style>{sheet}</style></head><body>{element}x</body></html>"
    )
    assert out == f"{expected}x"


def test_style_for_another_medium_is_not_inlined() -> None:
    out = normalize_quoted_html(
        '<html><head><style media="print">.a{display:none}</style>'
        '<style media="only screen and (max-width:480px)">.b{display:none}</style>'
        '<style media="screen">.c{color:red}</style></head>'
        '<body><p class="a">a</p><p class="b">b</p><p class="c">c</p></body></html>'
    )
    assert out == (
        '<p class="a">a</p><p class="b">b</p><p class="c" style="color:red">c</p>'
    )


def test_body_colour_still_reaches_a_table() -> None:
    """Without a doctype a table takes its colour from <body>, not from a div."""
    out = normalize_quoted_html(
        "<html><head><style>body{color:#fff;background:#222}</style></head>"
        "<body><table><tr><td>cell</td></tr></table></body></html>"
    )
    assert out == (
        '<div style="color:#fff;background:#222">'
        '<table style="color:inherit"><tr><td>cell</td></tr></table></div>'
    )


def test_style_sheet_that_would_multiply_is_not_inlined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gmail_quoted_html, "_MIN_INLINE_BUDGET", 100)
    sheet = "".join(f"p{{margin-top:{n}px}}" for n in range(50))
    body = "<p>x</p>" * 50
    out = normalize_quoted_html(
        f"<html><head><style>{sheet}</style></head><body>{body}</body></html>"
    )
    assert out == body


@pytest.mark.parametrize(
    "source",
    [
        "<html><body>" + "<!--[if x]>-->" * 50_000,
        "<html><body>" + "<style>" * 50_000,
        "<html><head><style>" + "/* " * 50_000 + "</style></head><body>",
        "<html><head><style>p{a:" + "(" * 50_000 + "}</style></head><body>",
    ],
    ids=["conditional-comments", "style-tags", "css-comments", "css-brackets"],
)
def test_unterminated_markup_is_handled_in_linear_time(source: str) -> None:
    """Inbound HTML is untrusted: nothing may rescan the rest of the document."""
    start = time.perf_counter()
    normalize_quoted_html(source)
    assert time.perf_counter() - start < 0.5


def test_parser_failure_keeps_the_original(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: Any, data: str) -> None:
        raise RuntimeError("parser failure")

    monkeypatch.setattr(gmail_quoted_html._DocumentFlattener, "feed", fail)
    assert normalize_quoted_html(OUTLOOK_DOCUMENT) is OUTLOOK_DOCUMENT


def test_reply_builder_nests_a_fragment() -> None:
    plain, html_part = _build_web_reply_bodies(
        REPLY,
        f'<div dir="ltr">{REPLY}</div>',
        {**PARENT, "text_body": "", "html_body": OUTLOOK_DOCUMENT},
    )

    assert not DOCUMENT_MARKUP.search(html_part), html_part
    assert html_part.startswith(f'<div dir="ltr">{REPLY}</div><br>')
    assert 'padding-left:1ex"><div lang="EN-GB"' in html_part
    # The plain quote derived from the HTML no longer starts with its <title>.
    assert plain.startswith(f"{REPLY}\n\nOn Tue, 10 Nov 2026 at 14:05")
    assert "wrote:\n\n> Dear Grace," in plain
    assert "Quarterly figures" not in plain


def test_forward_builder_nests_a_fragment() -> None:
    _subject, plain, html_part = _build_forward_content(
        {
            "Subject": "Quarterly figures",
            "From": PARENT["from"],
            "Date": PARENT["date"],
            "To": "user@example.com",
        },
        {"text": "", "html": OUTLOOK_DOCUMENT},
        None,
        "plain",
        None,
    )

    assert not DOCUMENT_MARKUP.search(html_part), html_part
    assert '<br></div><div lang="EN-GB"' in html_part
    assert "To: user@example.com\n\nDear Grace," in plain


def test_attachment_reply_builder_nests_a_fragment() -> None:
    """The EmailMessage path that replies with attachments still take."""
    original = {**PARENT, "text_body": "", "html_body": OUTLOOK_DOCUMENT}

    html_part = _build_quoted_reply_body(f"<p>{REPLY}</p>", "html", "", original)
    assert not DOCUMENT_MARKUP.search(html_part), html_part
    assert html_part.startswith(f"<p>{REPLY}</p>")

    plain = _build_quoted_reply_body(REPLY, "plain", "", original)
    assert "wrote:\n> Dear Grace," in plain
    assert "Quarterly figures" not in plain


def _unwrap(tool: Any) -> Any:
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _original_message() -> Dict[str, Any]:
    return {
        "id": "msg1",
        "threadId": "thread1",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Message-ID", "value": "<original@example.com>"},
                {"name": "Subject", "value": "Quarterly figures"},
                {"name": "From", "value": PARENT["from"]},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": PARENT["date"]},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _encode("Dear Grace,\n")}},
                {"mimeType": "text/html", "body": {"data": _encode(OUTLOOK_DOCUMENT)}},
            ],
        },
    }


def _gmail_service() -> Mock:
    service = Mock()
    users = service.users.return_value
    users.threads.return_value.get.return_value.execute.return_value = {
        "id": "thread1",
        "messages": [_original_message()],
    }
    users.messages.return_value.get.return_value.execute.return_value = (
        _original_message()
    )
    users.messages.return_value.send.return_value.execute.return_value = {"id": "s1"}
    users.drafts.return_value.create.return_value.execute.return_value = {"id": "d1"}
    # No Send-As entries: no signature and no display name to look up.
    users.settings.return_value.sendAs.return_value.list.return_value.execute.return_value = {
        "sendAs": []
    }
    return service


def _html_part(service: Mock, tool: Any) -> str:
    users = service.users.return_value
    if tool is send_gmail_message:
        raw = users.messages.return_value.send.call_args.kwargs["body"]["raw"]
    else:
        body = users.drafts.return_value.create.call_args.kwargs["body"]
        raw = body["message"]["raw"]
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    return "".join(
        part.get_payload(decode=True).decode("utf-8")
        for part in message.walk()
        if part.get_content_type() == "text/html"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [send_gmail_message, draft_gmail_message])
@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"body": REPLY}, id="plain"),
        pytest.param({"body": f"<p>{REPLY}</p>", "body_format": "html"}, id="html"),
        pytest.param(
            {
                "body": f"<p>{REPLY}</p>",
                "body_format": "html",
                "attachments": ATTACHMENT,
            },
            id="html-with-attachment",
        ),
    ],
)
async def test_quoted_reply_holds_no_nested_document(
    tool: Any, extra: Dict[str, Any]
) -> None:
    gmail = _gmail_service()

    await _unwrap(tool)(
        service=gmail,
        user_google_email="user@example.com",
        to="ada@example.com",
        subject="Re: Quarterly figures",
        thread_id="thread1",
        quote_original=True,
        include_signature=False,
        **extra,
    )

    html_part = _html_part(gmail, tool)
    assert not DOCUMENT_MARKUP.search(html_part), html_part
    assert html_part.index(REPLY) < html_part.index("Dear Grace,")
    assert f'<p class="MsoNormal" {MSO_NORMAL}>Dear Grace,' in html_part


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [send_gmail_message, draft_gmail_message])
@pytest.mark.parametrize("attachments", [None, ATTACHMENT], ids=["bare", "attachment"])
async def test_forward_holds_no_nested_document(
    tool: Any, attachments: Optional[List[Dict[str, str]]]
) -> None:
    gmail = _gmail_service()

    await _unwrap(tool)(
        service=gmail,
        user_google_email="user@example.com",
        to="grace@example.com",
        forward_message_id="msg1",
        body="FYI",
        attachments=attachments,
        include_signature=False,
    )

    html_part = _html_part(gmail, tool)
    assert not DOCUMENT_MARKUP.search(html_part), html_part
    assert html_part.index("FYI") < html_part.index("Dear Grace,")
    assert f'<p class="MsoNormal" {MSO_NORMAL}>Dear Grace,' in html_part
