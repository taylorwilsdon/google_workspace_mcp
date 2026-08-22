"""Link URL scheme validation for content written into Google Workspace documents.

Finding 26: the Markdown-to-Docs writer embedded a markdown link's ``href`` straight
into an ``updateTextStyle`` request, bypassing the scheme check that the
``format_text`` path applies. A ``javascript:`` or ``data:`` URL therefore persisted
into a shared document and was reproduced verbatim when the document was read back as
markdown.

Deciding on the raw string is not enough. Browsers (and Docs' own link handling)
tolerate a good deal of noise around a scheme -- leading whitespace, embedded tabs and
newlines, mixed case -- so the value is normalised first and only then matched against
an allowlist. Anything that does not survive normalisation into an allowed scheme is
refused rather than guessed at.
"""

import logging
import re
from typing import Optional, Tuple
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Schemes a document link may use. `mailto` is included because it is a normal thing
# to link to and carries no script execution.
ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})

# Schemes that execute or inline content when a link is followed. Listed separately
# from "not in the allowlist" purely so the rejection message can say why.
DANGEROUS_LINK_SCHEMES = frozenset(
    {"javascript", "data", "vbscript", "file", "blob", "about"}
)

# C0/C1 controls plus the Unicode space separators. Browsers strip tab, LF and CR from
# inside a scheme, so "java\tscript:" is a javascript URL to them; strip the whole
# class rather than just those three.
_STRIPPABLE = re.compile(r"[\x00-\x20\x7f-\xa0\u00ad\u200b-\u200f\u2028\u2029\ufeff]")

# How many times to percent-decode while looking for a hidden scheme. Two rounds
# catches `%256a...` (double-encoded) without letting a crafted input loop.
_MAX_DECODE_ROUNDS = 3


def _normalize_for_scheme_check(url: str) -> str:
    """Strip characters that a URL parser would ignore inside the scheme."""
    return _STRIPPABLE.sub("", url)


def _candidate_forms(url: str) -> list[str]:
    """Return the normalised forms a consumer might end up parsing.

    Percent-decoding is applied repeatedly because an encoded scheme (``%6a`` for
    ``j``) is invisible to a single-pass check but may be decoded before use.
    """
    forms = []
    current = url
    for _ in range(_MAX_DECODE_ROUNDS):
        normalized = _normalize_for_scheme_check(current)
        if normalized not in forms:
            forms.append(normalized)
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return forms


def _scheme_of(url: str) -> str:
    try:
        return urlparse(url).scheme.lower()
    except ValueError:
        return ""


def validate_link_url(url: Optional[str]) -> Tuple[bool, str]:
    """Return ``(is_valid, error_message)`` for a document hyperlink.

    ``None`` is valid: it means "no link", which callers use to clear one.
    """
    if url is None:
        return True, ""

    if not isinstance(url, str):
        return False, f"link URL must be a string, got {type(url).__name__}"

    if not url.strip():
        return False, "link URL cannot be empty"

    forms = _candidate_forms(url)

    # Any form resolving to an executable scheme disqualifies the URL, even if the
    # form actually handed to the API looks harmless.
    for form in forms:
        scheme = _scheme_of(form)
        if scheme in DANGEROUS_LINK_SCHEMES:
            return (
                False,
                f"link URL uses the disallowed scheme '{scheme}'. "
                f"Allowed schemes: {', '.join(sorted(ALLOWED_LINK_SCHEMES))}",
            )

    primary = forms[0]
    scheme = _scheme_of(primary)
    if scheme not in ALLOWED_LINK_SCHEMES:
        return (
            False,
            f"link URL must use one of: {', '.join(sorted(ALLOWED_LINK_SCHEMES))} "
            f"(got {scheme or 'no scheme'})",
        )

    if scheme in ("http", "https") and not urlparse(primary).netloc:
        return False, "link URL must include a host"

    if scheme == "mailto" and not urlparse(primary).path:
        return False, "mailto link must include an address"

    return True, ""


def is_safe_link_url(url: Optional[str]) -> bool:
    """Convenience predicate over :func:`validate_link_url`."""
    is_valid, _ = validate_link_url(url)
    return is_valid


def sanitize_link_url(
    url: Optional[str], *, context: str = "document"
) -> Optional[str]:
    """Return ``url`` when it is safe to write as a hyperlink, else ``None``.

    Used where dropping the link is better than failing the whole operation: the text
    is still written, just not as a link.
    """
    if url is None:
        return None
    is_valid, reason = validate_link_url(url)
    if is_valid:
        return url
    logger.warning("Dropping unsafe link in %s: %s", context, reason)
    return None
