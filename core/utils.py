import base64
import io
import json
import logging
import os
import posixpath
import re
import tempfile
import urllib.parse
import zipfile
import ssl
import asyncio
import functools

from pathlib import Path
from typing import Annotated, Any, List, Optional

from pydantic import BeforeValidator
from defusedxml import DefusedXmlException, ElementTree as ET

from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError
from .api_enablement import get_api_enablement_message
from auth.google_auth import GoogleAuthenticationError
from auth.oauth_config import is_oauth21_enabled, is_external_oauth21_provider

logger = logging.getLogger(__name__)

GOOGLE_API_WRITE_RETRIES = 3

_WORDPROCESSINGML_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
_DRAWINGML_NAMESPACES = {
    "http://schemas.openxmlformats.org/drawingml/2006/main",
    "http://purl.oclc.org/ooxml/drawingml/main",
}
_MARKUP_COMPATIBILITY_NAMESPACE = (
    "http://schemas.openxmlformats.org/markup-compatibility/2006"
)
_PACKAGE_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
_OFFICE_RELATIONSHIP_BASES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "https://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "http://purl.oclc.org/ooxml/officeDocument/relationships",
}
_WORD_TEXT_RELATIONSHIP_KINDS = {"header", "footer", "footnotes", "endnotes"}
_WORD_TEXT_RELATIONSHIP_TYPES = {
    f"{base}/{kind}": kind
    for base in _OFFICE_RELATIONSHIP_BASES
    for kind in _WORD_TEXT_RELATIONSHIP_KINDS
}
_SUPPORTED_TEXT_CHOICE_NAMESPACES = {
    *_WORDPROCESSINGML_NAMESPACES,
    *_DRAWINGML_NAMESPACES,
    "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "http://schemas.microsoft.com/office/word/2010/wordml",
    "http://schemas.microsoft.com/office/word/2012/wordml",
    "http://schemas.microsoft.com/office/drawing/2010/main",
}


class TransientNetworkError(Exception):
    """Custom exception for transient network errors after retries."""

    pass


class UserInputError(Exception):
    """Raised for user-facing input/validation errors that shouldn't be retried."""

    pass


def _coerce_json_str_to_type(v: Any, expected_type: type) -> Any:
    """Coerce a JSON-encoded string to a specific container type."""
    if not isinstance(v, str):
        return v

    try:
        parsed = json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v

    return parsed if isinstance(parsed, expected_type) else v


def _coerce_json_str_to_list(v: Any) -> Any:
    """Coerce a JSON-encoded string to a list.

    Some MCP clients (e.g. Cowork) serialise array parameters as JSON strings
    rather than native arrays.  This ``BeforeValidator`` transparently converts
    ``'["a","b"]'`` → ``["a", "b"]`` so Pydantic validation succeeds.
    """
    return _coerce_json_str_to_type(v, list)


StringList = Annotated[List[str], BeforeValidator(_coerce_json_str_to_list)]
"""``List[str]`` that also accepts a JSON-encoded string of an array.

Use in tool signatures instead of ``List[str]`` to work around MCP clients
that send ``'["value"]'`` instead of ``["value"]``.
"""


DictList = Annotated[List[dict[str, Any]], BeforeValidator(_coerce_json_str_to_list)]
"""``List[dict]`` that also accepts a JSON-encoded string of an array.

Use in tool signatures instead of ``List[dict]`` to work around MCP clients
that send ``'[{"key":"val"}]'`` instead of ``[{"key":"val"}]``.
"""


ObjectList = Annotated[List[object], BeforeValidator(_coerce_json_str_to_list)]
"""``List[object]`` that also accepts a JSON-encoded string of an array."""


def _coerce_json_str_to_dict(v: Any) -> Any:
    """Coerce a JSON-encoded string to a dict.

    Some MCP clients serialise dict parameters as JSON strings rather than
    native objects.  This ``BeforeValidator`` transparently converts
    ``'{"key":"val"}'`` -> ``{"key": "val"}`` so Pydantic validation succeeds.
    """
    return _coerce_json_str_to_type(v, dict)


JsonDict = Annotated[dict[str, Any], BeforeValidator(_coerce_json_str_to_dict)]
"""``dict`` that also accepts a JSON-encoded string of an object.

Use in tool signatures instead of ``Dict[str, Any]`` to work around MCP clients
that send ``'{"key":"val"}'`` instead of ``{"key": "val"}``.
"""


# Directories from which local file reads are allowed.
# By default, only the managed attachment storage directory is trusted.
# Override via ALLOWED_FILE_DIRS env var (os.pathsep-separated paths).
_ALLOWED_FILE_DIRS_ENV = "ALLOWED_FILE_DIRS"


def _get_allowed_file_dirs() -> list[Path]:
    """Return the list of directories from which local file access is permitted."""
    from core.attachment_storage import STORAGE_DIR

    allowed_dirs: list[Path] = [STORAGE_DIR]
    env_val = os.environ.get(_ALLOWED_FILE_DIRS_ENV)
    if env_val:
        allowed_dirs.extend(
            Path(p_stripped).expanduser().resolve()
            for p in env_val.split(os.pathsep)
            if (p_stripped := p.strip())
        )

    unique_dirs: list[Path] = []
    seen: set[Path] = set()
    for path in allowed_dirs:
        if path in seen:
            continue
        seen.add(path)
        unique_dirs.append(path)
    return unique_dirs


def validate_file_path(file_path: str) -> Path:
    """
    Validate that a file path is safe to read from the server filesystem.

    Resolves the path canonically (following symlinks), then verifies it falls
    within one of the allowed base directories. Rejects paths to sensitive
    system locations regardless of allowlist.

    Args:
        file_path: The raw file path string to validate.

    Returns:
        Path: The resolved, validated Path object.

    Raises:
        ValueError: If the path is outside allowed directories or targets
                    a sensitive location.
    """
    resolved = Path(file_path).resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"Path does not exist: {resolved}")

    # Block sensitive file patterns regardless of allowlist
    resolved_str = str(resolved)
    file_name = resolved.name.lower()

    path_parts = [part.lower() for part in resolved.parts]

    # Block .env files and variants (.env, .env.local, .env.production, etc.)
    if any(part == ".env" or part.startswith(".env.") for part in path_parts):
        raise ValueError(
            f"Access to '{resolved_str}' is not allowed: "
            ".env files may contain secrets and cannot be read, uploaded, or attached."
        )

    # Block well-known sensitive system paths (including macOS /private variants)
    sensitive_prefixes = (
        "/proc",
        "/sys",
        "/dev",
        "/etc/shadow",
        "/etc/passwd",
        "/private/etc/shadow",
        "/private/etc/passwd",
    )
    for prefix in sensitive_prefixes:
        if resolved_str == prefix or resolved_str.startswith(prefix + "/"):
            raise ValueError(
                f"Access to '{resolved_str}' is not allowed: "
                "path is in a restricted system location."
            )

    # Block sensitive directories that commonly contain credentials/keys.
    if ".ssh" in path_parts or ".aws" in path_parts:
        raise ValueError(
            f"Access to '{resolved_str}' is not allowed: "
            "path is in a directory that commonly contains secrets or credentials."
        )

    home = Path.home()
    sensitive_home_dirs = (
        ".kube",
        ".gnupg",
        ".config/gcloud",
    )
    for sensitive_dir in sensitive_home_dirs:
        blocked = home / sensitive_dir
        if resolved == blocked or str(resolved).startswith(str(blocked) + "/"):
            raise ValueError(
                f"Access to '{resolved_str}' is not allowed: "
                "path is in a directory that commonly contains secrets or credentials."
            )

    # Block other credential/secret file patterns
    sensitive_names = {
        ".credentials",
        ".credentials.json",
        "credentials.json",
        "client_secret.json",
        "client_secrets.json",
        "service_account.json",
        "service-account.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
        ".docker/config.json",
    }
    if file_name in sensitive_names:
        raise ValueError(
            f"Access to '{resolved_str}' is not allowed: "
            "this file commonly contains secrets or credentials."
        )

    allowed_dirs = _get_allowed_file_dirs()
    if not allowed_dirs:
        raise ValueError(
            "No allowed file directories configured. "
            "Set the ALLOWED_FILE_DIRS environment variable or configure "
            "WORKSPACE_ATTACHMENT_DIR."
        )

    for allowed in allowed_dirs:
        try:
            resolved.relative_to(allowed)
            return resolved
        except ValueError:
            continue

    raise ValueError(
        f"Access to '{resolved_str}' is not allowed: "
        f"path is outside permitted directories ({', '.join(str(d) for d in allowed_dirs)}). "
        "Set ALLOWED_FILE_DIRS to adjust."
    )


def check_credentials_directory_permissions(credentials_dir: str = None) -> None:
    """
    Check if the service has appropriate permissions to create and write to the .credentials directory.

    Args:
        credentials_dir: Path to the credentials directory (default: uses get_default_credentials_dir())

    Raises:
        PermissionError: If the service lacks necessary permissions
        OSError: If there are other file system issues
    """
    if credentials_dir is None:
        from auth.google_auth import get_default_credentials_dir

        credentials_dir = get_default_credentials_dir()

    # Multiple server processes may initialize the same credentials directory at
    # once. Keep the check idempotent: create the directory if needed, probe with
    # a unique temporary file, and never remove the shared directory on failure.
    try:
        os.makedirs(credentials_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=credentials_dir, prefix=".permission_test_"
        ) as probe:
            probe.write(b"test")
            probe.flush()
    except (PermissionError, OSError) as e:
        raise PermissionError(
            f"Cannot create or write to credentials directory "
            f"'{os.path.abspath(credentials_dir)}': {e}"
        )

    # Debug level: the startup screen already reports this check and the path.
    logger.debug(
        f"Credentials directory permissions check passed: {os.path.abspath(credentials_dir)}"
    )


def _xml_name(tag: str) -> tuple[Optional[str], str]:
    """Return an ElementTree tag's namespace URI and local name."""
    if tag.startswith("{") and "}" in tag:
        namespace, local_name = tag[1:].split("}", 1)
        return namespace, local_name
    return None, tag


def _parse_xml_with_choice_namespaces(
    xml_content: bytes,
) -> tuple[Any, dict[Any, dict[str, str]]]:
    """Parse XML and retain the in-scope prefix map for each mc:Choice."""
    namespaces: dict[str, str] = {}
    namespace_stack: list[tuple[str, Any]] = []
    choice_namespaces: dict[Any, dict[str, str]] = {}
    missing = object()
    xml_root = None

    for event, value in ET.iterparse(
        io.BytesIO(xml_content), events=("start-ns", "start", "end-ns")
    ):
        if event == "start-ns":
            prefix, uri = value
            prefix = prefix or ""
            namespace_stack.append((prefix, namespaces.get(prefix, missing)))
            namespaces[prefix] = uri
        elif event == "end-ns":
            prefix, previous = namespace_stack.pop()
            if previous is missing:
                namespaces.pop(prefix, None)
            else:
                namespaces[prefix] = previous
        else:
            element = value
            if xml_root is None:
                xml_root = element
            if element.tag == f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}Choice":
                choice_namespaces[element] = namespaces.copy()

    if xml_root is None:
        raise ET.ParseError("XML member has no root element")
    return xml_root, choice_namespaces


def _resolve_internal_part_target(source_part: str, target: str) -> Optional[str]:
    """Resolve an OPC internal relationship target to a ZIP member name."""
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None

    target_path = urllib.parse.unquote(parsed.path)
    if "\\" in target_path:
        return None
    if target_path.startswith("/"):
        resolved = posixpath.normpath(target_path.lstrip("/"))
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_part), target_path)
        )
    if resolved in {"", ".", ".."} or resolved.startswith("../"):
        return None
    return resolved


def _relationship_id(element: Any) -> Optional[str]:
    """Return an r:id attribute from either Transitional or Strict OOXML."""
    for attribute, value in element.attrib.items():
        namespace, local_name = _xml_name(attribute)
        if namespace in _OFFICE_RELATIONSHIP_BASES and local_name == "id":
            return value
    return None


def _word_related_text_targets(zf: zipfile.ZipFile, document_root: Any) -> list[str]:
    """Return active Word text parts using OPC relationships, not filenames."""
    try:
        relationships_root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError, DefusedXmlException):
        return []

    relationships: list[tuple[str, str, str]] = []
    relationships_by_id: dict[str, tuple[str, str]] = {}
    for relationship in relationships_root.iter():
        if _xml_name(relationship.tag) != (
            _PACKAGE_RELATIONSHIPS_NAMESPACE,
            "Relationship",
        ):
            continue
        if relationship.get("TargetMode", "Internal").lower() != "internal":
            continue
        relationship_id = relationship.get("Id")
        kind = _WORD_TEXT_RELATIONSHIP_TYPES.get(relationship.get("Type", ""))
        target = relationship.get("Target")
        if not relationship_id or not kind or not target:
            continue
        resolved = _resolve_internal_part_target("word/document.xml", target)
        if resolved is None:
            logger.warning(
                "Ignoring invalid internal Word relationship target %r", target
            )
            continue
        relationships.append((relationship_id, kind, resolved))
        relationships_by_id[relationship_id] = (kind, resolved)

    active_header_footer_ids: dict[str, list[str]] = {"header": [], "footer": []}
    for element in document_root.iter():
        namespace, local_name = _xml_name(element.tag)
        if namespace not in _WORDPROCESSINGML_NAMESPACES or local_name not in {
            "headerReference",
            "footerReference",
        }:
            continue
        kind = "header" if local_name == "headerReference" else "footer"
        relationship_id = _relationship_id(element)
        if relationship_id and relationship_id not in active_header_footer_ids[kind]:
            active_header_footer_ids[kind].append(relationship_id)

    targets: list[str] = []
    seen: set[str] = set()

    def add_target(target: str) -> None:
        if target not in seen:
            seen.add(target)
            targets.append(target)

    for kind in ("header", "footer"):
        for relationship_id in active_header_footer_ids[kind]:
            relationship = relationships_by_id.get(relationship_id)
            if relationship is not None and relationship[0] == kind:
                add_target(relationship[1])

    for wanted_kind in ("footnotes", "endnotes"):
        for _, kind, target in relationships:
            if kind == wanted_kind:
                add_target(target)

    return targets


def _branch_has_extractable_text(branch: Any) -> bool:
    """Whether an AlternateContent branch has text or an explicit separator."""
    parents = {child: parent for parent in branch.iter() for child in parent}
    for element in branch.iter():
        namespace, local_name = _xml_name(element.tag)
        if local_name == "t" and element.text:
            return True
        if namespace in _WORDPROCESSINGML_NAMESPACES and local_name in {
            "tab",
            "br",
            "cr",
        }:
            parent = parents.get(element)
            if parent is not None and _xml_name(parent.tag)[1] == "r":
                return True
        if namespace in _DRAWINGML_NAMESPACES and local_name == "br":
            parent = parents.get(element)
            if parent is not None and _xml_name(parent.tag)[1] == "p":
                return True
    return False


def _alternate_content_skip_set(
    xml_root: Any, choice_namespaces: dict[Any, dict[str, str]]
) -> set[int]:
    """Return node IDs belonging to unselected mc:AlternateContent branches."""
    alternate_tag = f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}AlternateContent"
    choice_tag = f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}Choice"
    fallback_tag = f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}Fallback"
    skip: set[int] = set()

    for alternate in xml_root.iter(alternate_tag):
        children = list(alternate)
        choices = [child for child in children if child.tag == choice_tag]
        fallback = next(
            (child for child in children if child.tag == fallback_tag), None
        )
        selected = None
        last_resort = None
        for choice in choices:
            prefixes = choice.get("Requires", "").split()
            namespaces = choice_namespaces.get(choice, {})
            if prefixes and all(
                namespaces.get(prefix) in _SUPPORTED_TEXT_CHOICE_NAMESPACES
                for prefix in prefixes
            ):
                if _branch_has_extractable_text(choice):
                    selected = choice
                    break
            elif last_resort is None and _branch_has_extractable_text(choice):
                last_resort = choice
        if selected is None and fallback is not None:
            selected = fallback
        if selected is None and last_resort is not None:
            selected = last_resort

        for branch in choices + ([fallback] if fallback is not None else []):
            if branch is selected:
                continue
            skip.update(id(node) for node in branch.iter())

    return skip


def extract_office_xml_text(file_bytes: bytes, mime_type: str) -> Optional[str]:
    """
    Very light-weight XML scraper for Word, Excel, PowerPoint files.
    Returns plain-text if something readable is found, else None.
    Uses zipfile + defusedxml.ElementTree.
    """
    shared_strings: List[str] = []
    ns_excel_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            targets: List[str] = []
            parsed_members: dict[str, tuple[Any, dict[Any, dict[str, str]]]] = {}
            # Map MIME → iterable of XML files to inspect
            if (
                mime_type
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ):
                targets = ["word/document.xml"]
                try:
                    document_content = zf.read("word/document.xml")
                except KeyError:
                    # The normal member-processing path below owns reporting a
                    # missing main part.
                    pass
                else:
                    document_root, choice_namespaces = (
                        _parse_xml_with_choice_namespaces(document_content)
                    )
                    parsed_members["word/document.xml"] = (
                        document_root,
                        choice_namespaces,
                    )
                    targets.extend(_word_related_text_targets(zf, document_root))
            elif (
                mime_type
                == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ):
                targets = [n for n in zf.namelist() if n.startswith("ppt/slides/slide")]
            elif (
                mime_type
                == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ):
                targets = [
                    n
                    for n in zf.namelist()
                    if n.startswith("xl/worksheets/sheet") and "drawing" not in n
                ]
                # Attempt to parse sharedStrings.xml for Excel files
                try:
                    shared_strings_xml = zf.read("xl/sharedStrings.xml")
                    shared_strings_root = ET.fromstring(shared_strings_xml)
                    for si_element in shared_strings_root.findall(
                        f"{{{ns_excel_main}}}si"
                    ):
                        text_parts = []
                        # Find all <t> elements, simple or within <r> runs, and concatenate their text
                        for t_element in si_element.findall(f".//{{{ns_excel_main}}}t"):
                            if t_element.text:
                                text_parts.append(t_element.text)
                        shared_strings.append("".join(text_parts))
                except KeyError:
                    logger.info(
                        "No sharedStrings.xml found in Excel file (this is optional)."
                    )
                except ET.ParseError as e:
                    logger.error(f"Error parsing sharedStrings.xml: {e}")
                except (
                    Exception
                ) as e:  # Catch any other unexpected error during sharedStrings parsing
                    logger.error(
                        f"Unexpected error processing sharedStrings.xml: {e}",
                        exc_info=True,
                    )
            else:
                return None

            pieces: List[str] = []
            for member in targets:
                try:
                    if member in parsed_members:
                        xml_root, choice_namespaces = parsed_members[member]
                    else:
                        xml_content = zf.read(member)
                        if (
                            mime_type
                            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        ):
                            xml_root = ET.fromstring(xml_content)
                            choice_namespaces = {}
                        else:
                            xml_root, choice_namespaces = (
                                _parse_xml_with_choice_namespaces(xml_content)
                            )
                    member_texts: List[str] = []

                    if (
                        mime_type
                        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ):
                        for cell_element in xml_root.findall(
                            f".//{{{ns_excel_main}}}c"
                        ):  # Find all <c> elements
                            value_element = cell_element.find(
                                f"{{{ns_excel_main}}}v"
                            )  # Find <v> under <c>

                            # Skip if cell has no value element or value element has no text
                            if value_element is None or value_element.text is None:
                                continue

                            cell_type = cell_element.get("t")
                            if cell_type == "s":  # Shared string
                                try:
                                    ss_idx = int(value_element.text)
                                    if 0 <= ss_idx < len(shared_strings):
                                        member_texts.append(shared_strings[ss_idx])
                                    else:
                                        logger.warning(
                                            f"Invalid shared string index {ss_idx} in {member}. Max index: {len(shared_strings) - 1}"
                                        )
                                except ValueError:
                                    logger.warning(
                                        f"Non-integer shared string index: '{value_element.text}' in {member}."
                                    )
                            else:  # Direct value (number, boolean, inline string if not 's')
                                member_texts.append(value_element.text)
                    else:  # Word or PowerPoint
                        # Runs belonging to one paragraph concatenate without an
                        # invented separator. Word routinely splits a token across
                        # runs for formatting, spell-check state, and tracked
                        # changes; real spacing is carried by the text itself.
                        #
                        # Nested text-box paragraphs interrupt their outer
                        # paragraph. Flushing when the closest owner changes keeps
                        # XML order without attributing the nested text twice.
                        parents = {
                            child: parent
                            for parent in xml_root.iter()
                            for child in parent
                        }

                        def _line_owner(node):
                            """Closest paragraph ancestor, else nearest non-run container."""
                            cur = parents.get(node)
                            nearest_non_run = None
                            while cur is not None:
                                _, local_name = _xml_name(cur.tag)
                                if local_name == "p":
                                    return cur
                                if nearest_non_run is None and local_name != "r":
                                    nearest_non_run = cur
                                cur = parents.get(cur)
                            return nearest_non_run

                        skip = _alternate_content_skip_set(xml_root, choice_namespaces)
                        current_owner = None
                        current_parts: list[str] = []

                        def flush_line() -> None:
                            if not current_parts:
                                return
                            # Text-space padding is normalized at paragraph
                            # boundaries, but explicit tabs/breaks remain content.
                            line = "".join(current_parts).strip(" ")
                            if line:
                                member_texts.append(line)

                        for node in xml_root.iter():
                            if id(node) in skip:
                                continue
                            namespace, local_name = _xml_name(node.tag)
                            piece = None
                            if local_name == "t" and node.text:
                                piece = node.text
                            elif namespace in _WORDPROCESSINGML_NAMESPACES and (
                                local_name in {"tab", "br", "cr"}
                            ):
                                # A Word tab/break is content only directly under
                                # w:r; w:tab under w:pPr/w:tabs is a tab stop.
                                parent = parents.get(node)
                                if (
                                    parent is not None
                                    and _xml_name(parent.tag)[1] == "r"
                                ):
                                    piece = "\t" if local_name == "tab" else "\n"
                            elif (
                                namespace in _DRAWINGML_NAMESPACES
                                and local_name == "br"
                            ):
                                # DrawingML a:br is a direct a:p child between
                                # runs, unlike Word's w:br-under-w:r structure.
                                parent = parents.get(node)
                                if (
                                    parent is not None
                                    and _xml_name(parent.tag)[1] == "p"
                                ):
                                    piece = "\n"
                            if piece is None:
                                continue
                            owner = _line_owner(node)
                            if owner is None:
                                owner = node
                            if current_owner is not None and owner is not current_owner:
                                flush_line()
                                current_parts = []
                            current_owner = owner
                            current_parts.append(piece)

                        flush_line()

                    if member_texts:
                        # Word/PowerPoint entries are one paragraph each, so join
                        # them with newlines: paragraph boundaries carry meaning,
                        # and collapsing them runs headings into body text.
                        # Spreadsheet entries stay space-joined as before.
                        sep = (
                            " "
                            if mime_type
                            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            else "\n"
                        )
                        pieces.append(sep.join(member_texts))

                except ET.ParseError as e:
                    logger.warning(
                        f"Could not parse XML in member '{member}' for {mime_type} file: {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error processing member '{member}' for {mime_type}: {e}",
                        exc_info=True,
                    )
                    # continue processing other members

            if not pieces:  # If no text was extracted at all
                return None

            # Join content from different members (sheets/slides) with double newlines for separation
            text = "\n\n".join(pieces).strip(" ")
            return text or None  # Ensure None is returned if text is empty after strip

    except zipfile.BadZipFile:
        logger.warning(f"File is not a valid ZIP archive (mime_type: {mime_type}).")
        return None
    except (
        ET.ParseError
    ) as e:  # Catch parsing errors at the top level if zipfile itself is XML-like
        logger.error(f"XML parsing error at a high level for {mime_type}: {e}")
        return None
    except Exception as e:
        logger.error(
            f"Failed to extract office XML text for {mime_type}: {e}", exc_info=True
        )
        return None


IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
}


def extract_pdf_text(file_bytes: bytes) -> Optional[str]:
    """
    Extract text from a PDF using pypdf.
    Returns plain text with pages separated by double newlines, or None on failure.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        if not pages:
            return None
        return "\n\n".join(pages).strip() or None
    except Exception as e:
        logger.warning(f"Failed to extract PDF text: {e}")
        return None


def encode_image_content(file_bytes: bytes, mime_type: str) -> str:
    """
    Base64-encode image bytes with a mime type metadata prefix.

    Args:
        file_bytes: The image file content as bytes.
        mime_type: The MIME type of the image (must start with "image/").

    Returns:
        str: Base64-encoded image with mime type prefix.

    Raises:
        ValueError: If mime_type is not an image MIME type.
    """
    if not mime_type.startswith("image/"):
        raise ValueError(
            f"Expected image/* MIME type, got '{mime_type}'. "
            "Only image content can be base64-encoded for multimodal clients."
        )
    encoded = base64.b64encode(file_bytes).decode("ascii")
    return f"[base64_image:{mime_type}]{encoded}"


_URL_QUERY_RE = re.compile(r"\?.*?(?=\s+returned(?:\s|$)|[>\"']|$)")


def _scrub_url_queries(text: str) -> str:
    """Strip query strings from any URL embedded in ``text`` before logging.

    ``HttpError.__str__`` includes the full request URI, whose query string
    carries user content (e.g. ``.../messages?q=<the user's search terms>``)
    and can carry signed-URL secrets. The scheme/host/path identify the
    failing endpoint, which is all the log needs.
    """
    return _URL_QUERY_RE.sub("?<query-redacted>", text)


def _format_http_error_for_log(error: HttpError) -> str:
    """Return operational HTTP failure context without response-body text."""
    status = getattr(error.resp, "status", "unknown")
    uri = getattr(error, "uri", None)
    request = _scrub_url_queries(uri) if uri else "<unknown>"
    return f"status={status}, request={request}"


def handle_http_errors(
    tool_name: str, is_read_only: bool = False, service_type: Optional[str] = None
):
    """
    A decorator to handle Google API HttpErrors and transient SSL errors in a standardized way.

    It wraps a tool function, catches HttpError, logs a detailed error message,
    and raises a generic Exception with a user-friendly message.

    If is_read_only is True, it will also catch ssl.SSLError and retry with
    exponential backoff. After exhausting retries, it raises a TransientNetworkError.

    Args:
        tool_name (str): The name of the tool being decorated (e.g., 'list_calendars').
        is_read_only (bool): If True, the operation is considered safe to retry on
                             transient network errors. Defaults to False.
        service_type (str): Optional. The Google service type (e.g., 'calendar', 'gmail').
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            max_retries = 3
            base_delay = 1

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except ssl.SSLError as e:
                    if is_read_only and attempt < max_retries - 1:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            f"SSL error in {tool_name} on attempt {attempt + 1}: {e}. Retrying in {delay} seconds..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"SSL error in {tool_name} on final attempt: {e}. Raising exception."
                        )
                        raise TransientNetworkError(
                            f"A transient SSL error occurred in '{tool_name}' after {max_retries} attempts. "
                            "This is likely a temporary network or certificate issue. Please try again shortly."
                        ) from e
                except UserInputError as e:
                    message = f"Input error in {tool_name}: {e}"
                    logger.warning(message)
                    raise e
                except HttpError as error:
                    user_google_email = kwargs.get("user_google_email", "N/A")
                    error_details = str(error)

                    # Check if this is an API not enabled error
                    if (
                        error.resp.status == 403
                        and "accessNotConfigured" in error_details
                    ):
                        enablement_msg = get_api_enablement_message(
                            error_details, service_type
                        )

                        if enablement_msg:
                            message = (
                                f"API error in {tool_name}: {enablement_msg}\n\n"
                                f"User: {user_google_email}"
                            )
                        else:
                            message = (
                                f"API error in {tool_name}: {error}. "
                                f"The required API is not enabled for your project. "
                                f"Please check the Google Cloud Console to enable it."
                            )
                    elif error.resp.status in [401, 403]:
                        # Authentication/authorization errors
                        if is_oauth21_enabled():
                            if is_external_oauth21_provider():
                                auth_hint = (
                                    "LLM: Ask the user to provide a valid OAuth 2.1 "
                                    "bearer token in the Authorization header and retry."
                                )
                            else:
                                auth_hint = (
                                    "LLM: Ask the user to authenticate via their MCP "
                                    "client's OAuth 2.1 flow and retry."
                                )
                        else:
                            auth_hint = (
                                "LLM: Try 'start_google_auth' with the user's email "
                                "and the appropriate service_name."
                            )
                        message = (
                            f"API error in {tool_name}: {error}. "
                            f"You might need to re-authenticate for user '{user_google_email}'. "
                            f"{auth_hint}"
                        )
                    else:
                        # Other HTTP errors (400 Bad Request, etc.) - don't suggest re-auth
                        message = f"API error in {tool_name}: {error}"

                    # ERROR gets the scrubbed form (HttpError embeds the request
                    # URI and may echo user content in its response details);
                    # the full exception with traceback stays at DEBUG.
                    logger.error(
                        f"API error in {tool_name}: {_format_http_error_for_log(error)}"
                    )
                    logger.debug(f"API error detail in {tool_name}", exc_info=True)
                    raise Exception(message) from error
                except TransientNetworkError:
                    # Re-raise without wrapping to preserve the specific error type
                    raise
                except ToolError:
                    # Re-raise explicit tool errors so FastMCP can surface them directly.
                    raise
                except GoogleAuthenticationError:
                    # Re-raise authentication errors without wrapping
                    raise
                except Exception as e:
                    message = f"An unexpected error occurred in {tool_name}: {e}"
                    logger.exception(message)
                    raise Exception(message) from e

        # Propagate _required_google_scopes if present (for tool filtering)
        if hasattr(func, "_required_google_scopes"):
            wrapper._required_google_scopes = func._required_google_scopes

        return wrapper

    return decorator
