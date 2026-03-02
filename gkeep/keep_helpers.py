"""
Google Keep Helper Functions

Shared utilities for Google Keep operations.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ListItem:
    """A single checklist item in a Keep note."""

    text: str
    checked: bool = False
    children: List["ListItem"] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "ListItem":
        children = [
            cls.from_api(child) for child in data.get("childListItems", [])
        ]
        return cls(
            text=data.get("text", {}).get("text", ""),
            checked=data.get("checked", False),
            children=children,
        )


@dataclass
class Attachment:
    """An attachment on a Keep note."""

    name: str
    mime_types: List[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Attachment":
        return cls(
            name=data.get("name", ""),
            mime_types=data.get("mimeType", []),
        )


@dataclass
class Permission:
    """A permission entry on a Keep note."""

    name: str
    role: str
    email: str

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Permission":
        return cls(
            name=data.get("name", ""),
            role=data.get("role", ""),
            email=data.get("email", ""),
        )


@dataclass
class Note:
    """A Google Keep note."""

    name: str
    title: str
    text: Optional[str] = None
    list_items: Optional[List[ListItem]] = None
    trashed: bool = False
    trash_time: Optional[str] = None
    create_time: Optional[str] = None
    update_time: Optional[str] = None
    attachments: List[Attachment] = field(default_factory=list)
    permissions: List[Permission] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Note":
        body = data.get("body", {})
        text_content = body.get("text", {})
        list_content = body.get("list", {})

        text = text_content.get("text") if text_content else None
        list_items = (
            [ListItem.from_api(item) for item in list_content["listItems"]]
            if list_content and list_content.get("listItems")
            else None
        )

        return cls(
            name=data.get("name", ""),
            title=data.get("title", ""),
            text=text,
            list_items=list_items,
            trashed=data.get("trashed", False),
            trash_time=data.get("trashTime"),
            create_time=data.get("createTime"),
            update_time=data.get("updateTime"),
            attachments=[
                Attachment.from_api(att) for att in data.get("attachments", [])
            ],
            permissions=[
                Permission.from_api(perm) for perm in data.get("permissions", [])
            ],
        )


def format_note(note: Note) -> str:
    """Format a Note into a human-readable summary string."""
    lines = []
    lines.append(f"- Name: {note.name or 'N/A'}")
    lines.append(f"  Title: {note.title or 'Untitled'}")

    if note.text:
        preview = note.text[:200] + ("..." if len(note.text) > 200 else "")
        lines.append(f"  Body (text): {preview}")
    elif note.list_items is not None:
        lines.append(f"  Body (list): {len(note.list_items)} item(s)")
        for item in note.list_items[:10]:
            marker = "[x]" if item.checked else "[ ]"
            lines.append(f"    {marker} {item.text}")
            for child in item.children:
                child_marker = "[x]" if child.checked else "[ ]"
                lines.append(f"      {child_marker} {child.text}")
        if len(note.list_items) > 10:
            lines.append(f"    ... and {len(note.list_items) - 10} more item(s)")

    if note.trashed:
        lines.append(f"  Trashed: {note.trash_time or 'Yes'}")
    lines.append(f"  Created: {note.create_time or 'N/A'}")
    lines.append(f"  Updated: {note.update_time or 'N/A'}")

    if note.attachments:
        lines.append(f"  Attachments: {len(note.attachments)}")
        for att in note.attachments:
            lines.append(f"    - {att.name or 'N/A'} ({', '.join(att.mime_types)})")

    if note.permissions:
        lines.append(f"  Permissions: {len(note.permissions)}")
        for perm in note.permissions:
            lines.append(f"    - {perm.email or 'N/A'} ({perm.role or 'N/A'})")

    return "\n".join(lines)


def _format_list_item(item: ListItem, indent: str = "") -> List[str]:
    marker = "x" if item.checked else " "
    lines = [f"{indent}- [{marker}] {item.text}"]
    for child in item.children:
        lines.extend(_format_list_item(child, indent=f'  {indent}'))
    return lines


def format_note_content(note: Note) -> str:
    """Format only the content of a note (full text or checklist) without truncation."""
    lines = []
    if note.title:
        lines.append(f"Title: {note.title}")

    if note.text:
        lines.append(note.text)
    elif note.list_items is not None:
        for item in note.list_items:
            lines.extend(_format_list_item(item))
    else:
        lines.append("(empty note)")

    return "\n".join(lines)


def build_note_body(
    title: str,
    text: Optional[str] = None,
    list_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a note request body for creation."""
    body: Dict[str, Any] = {"title": title}
    if list_items is not None:
        body["body"] = {"list": {"listItems": list_items}}
    elif text is not None:
        body["body"] = {"text": {"text": text}}
    return body
