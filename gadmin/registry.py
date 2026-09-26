"""Pinned registry of callable Google Workspace admin operations.

Every admin call is described by a checked-in JSON file generated from a pinned
Google Discovery document revision. The registry is never fetched or extended at
tool-call time: an operation ID absent from these files cannot be invoked, and
parameters or body fields absent from its entry are rejected before dispatch.
"""

import copy
import json
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

REGISTRY_DIR = Path(__file__).parent / "registry"

RISK_LEVELS = ("read", "manage", "destructive")

# Why a pinned discovery method is not callable. Each registry file lists every
# method of its API as either an operation or an exclusion with one of these.
EXCLUSION_CATEGORIES = (
    "caller-url",
    "cloud-resource",
    "console-only",
    "deferred-family",
    "deprecated",
    "encryption-key",
    "mailbox-setting",
    "message-scope",
    "push-channel",
    "remote-control",
    "replacement-variant",
    "secret-exposure",
    "secret-in-payload",
    "sensitive-content",
    "tenant-config",
    "undocumented-scope",
    "unguarded-target",
    "unvalidated-schema",
)

# How an operation stays inside the verified customer; gadmin.boundary applies
# the rules. Anchor kinds tie the call to the customer on their own; the others
# only narrow a call that an anchor rule already bound.
_ANCHOR_KINDS = (
    "customer",
    "customer_path",
    "user",
    "account",
    "group",
    "domain",
    "resource",
    "org_unit",
    "response_user",
    "response_customer",
    "organization",
    "response_organization",
    "tenant",
    "global",
)
_BOUNDARY_KINDS = (
    *_ANCHOR_KINDS,
    "member",
    "role",
    "role_assignment",
    "deny",
    "policy_target",
    "policy_value",
    "listed",
    "response_unscoped",
    "customer_record",
    "custom_role",
    "custom_schema",
    "role_privileges",
)
_PARAM_KINDS = (
    "customer",
    "customer_path",
    "member",
    "role_assignment",
    "custom_role",
    "custom_schema",
    "deny",
    "organization",
)
# Kinds that verify a value with a registered read.
_READ_KINDS = ("resource", "policy_value", "listed")
_RESPONSE_KINDS = (
    "response_user",
    "response_customer",
    "response_organization",
    "response_unscoped",
)
_BARE_KINDS = ("tenant", "global")

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}

# Path parameters are emails, opaque IDs, or "my_customer". Anything else, such
# as a slash, query string, or URL, is refused rather than encoded into a path.
_SAFE_PATH_VALUE = re.compile(r"^[A-Za-z0-9@._+'\-]+$")


class UnknownOperation(KeyError):
    """The operation ID is not in the pinned registry."""


class InvalidOperationInput(ValueError):
    """Parameters or body do not match the registered operation schema."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    location: str = "body"
    # Parameters: required by Google. Body object members: must be present,
    # because the method replaces the whole resource.
    required: bool = False
    enum: tuple[str, ...] = ()
    # Body fields only: an array's element schema, and an object's exact members
    # (an object without listed members accepts any keys).
    items: "FieldSpec | None" = None
    fields: tuple["FieldSpec", ...] = ()
    # An anchored regular expression the value must match, replacing the
    # default path-value check for path parameters.
    pattern: str = ""
    # Body arrays only: the most elements accepted (0 for no limit).
    max_items: int = 0


@dataclass(frozen=True)
class BoundaryRule:
    kind: str
    param: str | None = None
    # A body field; dotted paths reach nested members and every array element.
    field: str | None = None
    # "id" or "email" for users and groups; "name" sets a customer as
    # "customers/{id}".
    as_: str = "id"
    # A user parameter may be the literal "all" (every user of the customer).
    allow_all: bool = False
    # A resource or policy value rule: the registered read that verifies the
    # name, applied to its first ``segments`` path segments (0 for the whole
    # name), after ``prefix`` turns a bare ID into the name the read takes.
    read: str | None = None
    segments: int = 0
    prefix: str = ""


@dataclass(frozen=True)
class OperationSpec:
    id: str
    service: str
    version: str
    resource_path: tuple[str, ...]
    method: str
    scopes: tuple[str, ...]
    risk: str
    params: tuple[FieldSpec, ...]
    body_fields: tuple[FieldSpec, ...]
    target_param: str | None
    risk_escalations: tuple[tuple[str, Any, str], ...]
    response_items_key: str | None
    response_fields: tuple[str, ...]
    discovery_revision: str
    boundary: tuple[BoundaryRule, ...] = ()

    @property
    def required_params(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.required)

    @property
    def allowed_body_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.body_fields)


@dataclass(frozen=True)
class Exclusion:
    id: str
    service: str
    version: str
    category: str
    reason: str
    discovery_revision: str


def _pattern(name: str, pattern: str) -> str:
    if pattern and not (pattern.startswith("^") and pattern.endswith("$")):
        raise ValueError(f"Registry pattern for {name} must be anchored")
    return pattern


def _body_field(name: str, schema: str | dict) -> FieldSpec:
    """Parse a body field declared as a type name, or as {"type", "items"} for
    an array or {"type": "object", "fields"} for an object with fixed members,
    optionally with an "enum", "pattern", "required", or an array's "max_items"."""
    if isinstance(schema, str):
        schema = {"type": schema}
    items = schema.get("items")
    field = FieldSpec(
        name=name,
        type=schema["type"],
        required=schema.get("required", False),
        enum=tuple(schema.get("enum", ())),
        items=None if items is None else _body_field(name, items),
        fields=tuple(_body_field(k, v) for k, v in schema.get("fields", {}).items()),
        pattern=_pattern(name, schema.get("pattern", "")),
        max_items=schema.get("max_items", 0),
    )
    is_array = field.type == "array"
    if (
        field.type not in _JSON_TYPES
        or is_array != bool(field.items)
        or (field.max_items and not is_array)
    ):
        raise ValueError(f"Invalid registry schema for body field {name}")
    return field


def _is_string_path(fields: tuple[FieldSpec, ...], path: str | None) -> bool:
    """Whether a dotted body path ends in a string or a list of strings."""
    if not path:
        return False
    field = None
    for name in path.split("."):
        field = next((f for f in fields if f.name == name), None)
        if field is None:
            return False
        if field.type == "array":
            field = field.items
        fields = field.fields
    return field.type == "string"


def _boundary(
    op_id: str, rules: list[dict], spec: "OperationSpec"
) -> tuple[BoundaryRule, ...]:
    """Parse and check an operation's boundary rules against its own schema."""
    params = {p.name for p in spec.params}
    customer_bound = any(rule["kind"] == "customer" for rule in rules)
    parsed: list[BoundaryRule] = []
    for rule in rules:
        r = BoundaryRule(
            rule["kind"],
            rule.get("param"),
            rule.get("field"),
            rule.get("as", "id"),
            rule.get("all", False),
            rule.get("read"),
            rule.get("segments", 0),
            rule.get("prefix", ""),
        )
        if r.kind in _RESPONSE_KINDS:
            valid = r.param is None and r.field in spec.response_fields
        elif r.kind == "customer_record":
            valid = r.param is None and r.field is None
        elif r.kind == "role_privileges":
            valid = (
                r.param is None
                and r.field == "rolePrivileges"
                and any(
                    f.name == r.field and f.type == "array" for f in spec.body_fields
                )
            )
        elif r.kind in _BARE_KINDS:
            valid = r.param is None and r.field is None
        elif r.kind == "customer" and r.field:
            # A customer name the server sets in a top-level body member.
            valid = r.as_ == "name" and r.param is None and "." not in r.field
            valid = valid and _is_string_path(spec.body_fields, r.field)
        elif r.kind in _PARAM_KINDS:
            valid = r.param in params and r.field is None
        elif r.kind == "listed":
            # A parameter the read must list in its response ``field``.
            valid = r.param in params and bool(r.field)
        else:
            in_body = _is_string_path(spec.body_fields, r.field)
            valid = (r.param in params) != in_body and not (r.param and r.field)
        if r.kind == "member":
            valid = valid and any(p.kind == "group" and p.param for p in parsed)
        if r.allow_all:
            valid = valid and r.kind == "user" and bool(r.param) and customer_bound
        # Only a read kind names a verifying read, its segments, and prefix.
        if (r.kind in _READ_KINDS) != bool(r.read) or r.segments < 0:
            valid = False
        elif (r.segments or r.prefix) and r.kind not in _READ_KINDS:
            valid = False
        forms = ("id", "email", "name") if r.kind == "customer" else ("id", "email")
        if r.kind not in _BOUNDARY_KINDS or r.as_ not in forms or not valid:
            raise ValueError(f"Invalid boundary rule {rule} for {op_id}")
        parsed.append(r)
    if not any(r.kind in _ANCHOR_KINDS for r in parsed):
        raise ValueError(f"Admin operation {op_id} has no customer boundary")
    return tuple(parsed)


def _load_operations(doc: dict) -> dict[str, OperationSpec]:
    specs = {}
    for op_id, entry in doc["operations"].items():
        risk = entry["risk"]
        escalations = tuple(
            (rule["field"], rule["equals"], rule["risk"])
            for rule in entry.get("risk_escalations", [])
        )
        if any(r not in RISK_LEVELS for r in (risk, *(e[2] for e in escalations))):
            raise ValueError(f"Invalid risk level in registry entry {op_id}")
        response = entry.get("response", {})
        spec = OperationSpec(
            id=op_id,
            service=doc["service"],
            version=doc["version"],
            resource_path=tuple(entry["resource_path"]),
            method=entry["method"],
            scopes=tuple(entry["scopes"]),
            risk=risk,
            params=tuple(
                FieldSpec(
                    name=name,
                    type=p["type"],
                    location=p["location"],
                    required=p.get("required", False),
                    enum=tuple(p.get("enum", ())),
                    pattern=_pattern(name, p.get("pattern", "")),
                )
                for name, p in entry["params"].items()
            ),
            body_fields=tuple(
                _body_field(name, schema)
                for name, schema in entry.get("body", {}).items()
            ),
            target_param=entry.get("target_param"),
            risk_escalations=escalations,
            response_items_key=response.get("items_key"),
            response_fields=tuple(response.get("fields", ())),
            discovery_revision=doc["discovery"]["revision"],
        )
        specs[op_id] = replace(
            spec, boundary=_boundary(op_id, entry.get("boundary", []), spec)
        )
    return specs


def _load_exclusions(doc: dict) -> dict[str, Exclusion]:
    exclusions = {}
    for op_id, entry in doc.get("excluded", {}).items():
        if entry["category"] not in EXCLUSION_CATEGORIES or not entry["reason"]:
            raise ValueError(f"Invalid exclusion for {op_id}")
        exclusions[op_id] = Exclusion(
            id=op_id,
            service=doc["service"],
            version=doc["version"],
            category=entry["category"],
            reason=entry["reason"],
            discovery_revision=doc["discovery"]["revision"],
        )
    return exclusions


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, OperationSpec], tuple[Exclusion, ...]]:
    specs: dict[str, OperationSpec] = {}
    exclusions: list[Exclusion] = []
    for path in sorted(REGISTRY_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        operations = _load_operations(doc)
        excluded = _load_exclusions(doc)
        # A method shared by several APIs (admin.channels.stop) may be excluded
        # in each of them, but an ID is never both registered and excluded.
        registered = set(specs) | set(operations)
        excluded_ids = {e.id for e in exclusions} | set(excluded)
        clashes = (set(operations) & set(specs)) | (registered & excluded_ids)
        if clashes:
            raise ValueError(f"Duplicate admin operation ID: {sorted(clashes)}")
        specs.update(operations)
        exclusions.extend(excluded.values())
    for spec in specs.values():
        _check_resource_reads(spec, specs)
    return specs, tuple(exclusions)


def _check_resource_reads(spec: OperationSpec, specs: dict) -> None:
    """A read rule names a registered read addressed by one parameter that
    proves ownership without read rules of its own, so checks never nest. A
    listed rule's read returns items that keep the listed field."""
    for rule in spec.boundary:
        if rule.kind not in _READ_KINDS:
            continue
        read = specs.get(rule.read)
        if (
            read is None
            or read.risk != "read"
            or read.target_param is None
            or any(r.kind in _READ_KINDS for r in read.boundary)
            or (
                rule.kind == "listed"
                and not (read.response_items_key and rule.field in read.response_fields)
            )
        ):
            raise ValueError(f"Invalid resource read {rule.read} for {spec.id}")


def _registry() -> dict[str, OperationSpec]:
    return _load()[0]


def excluded_operations() -> tuple[Exclusion, ...]:
    """Pinned discovery methods deliberately left uncallable, with the reason."""
    return _load()[1]


def get_exclusion(operation_id: str) -> Exclusion | None:
    """The first exclusion recorded for an ID, or None if it is not excluded."""
    return next((e for e in _load()[1] if e.id == operation_id), None)


def get_operation(operation_id: str) -> OperationSpec:
    try:
        return _registry()[operation_id]
    except KeyError:
        raise UnknownOperation(operation_id) from None


def registered_spec(operation: OperationSpec | str) -> OperationSpec:
    """Return the pinned spec for an ID or spec; a spec not loaded from the
    registry (for example one altered with dataclasses.replace) is refused."""
    operation_id = operation if isinstance(operation, str) else operation.id
    spec = get_operation(operation_id)
    if not isinstance(operation, str) and operation is not spec:
        raise UnknownOperation(operation_id)
    return spec


def iter_operations() -> Iterator[OperationSpec]:
    return iter(_registry().values())


def _check_value(field: FieldSpec, value: Any, kind: str) -> None:
    allowed = _JSON_TYPES[field.type]
    # bool is a subclass of int; an integer field must not accept True/False.
    if not isinstance(value, allowed) or (
        field.type == "integer" and isinstance(value, bool)
    ):
        raise InvalidOperationInput(f"{kind} '{field.name}' must be a {field.type}")
    if field.enum and value not in field.enum:
        raise InvalidOperationInput(
            f"{kind} '{field.name}' must be one of {list(field.enum)}"
        )
    if field.type == "array":
        if field.max_items and len(value) > field.max_items:
            raise InvalidOperationInput(
                f"{kind} '{field.name}' accepts at most {field.max_items} items"
            )
        for item in value:
            _check_value(field.items, item, kind)
        return
    if field.type == "object" and field.fields:
        _check_members(field.fields, value, field.name)
        return
    if field.type != "string":
        return
    if field.pattern:
        if not re.fullmatch(field.pattern, value):
            raise InvalidOperationInput(
                f"{kind} '{field.name}' does not have the expected format"
            )
    elif field.location == "path" and not _SAFE_PATH_VALUE.fullmatch(value):
        raise InvalidOperationInput(
            f"{kind} '{field.name}' must be an email address or ID"
        )
    # A URL is accepted only as an https value its registered pattern allows
    # (SAML and OIDC identity provider endpoints).
    if "://" in value and not (field.pattern and value.startswith("https://")):
        raise InvalidOperationInput(f"{kind} '{field.name}' must not contain a URL")


def _check_members(fields: tuple[FieldSpec, ...], value: dict, owner: str) -> None:
    known = {f.name: f for f in fields}
    unexpected = sorted(set(value) - set(known))
    if unexpected:
        raise InvalidOperationInput(f"Unexpected body fields for {owner}: {unexpected}")
    missing = [f.name for f in fields if f.required and f.name not in value]
    if missing:
        raise InvalidOperationInput(f"Missing body fields for {owner}: {missing}")
    for name, item in value.items():
        _check_value(known[name], item, "Body field")


def validate_call(
    spec: OperationSpec, params: dict | None, body: dict | None
) -> tuple[dict, dict | None]:
    """Return cleaned copies of ``params`` and ``body`` or raise InvalidOperationInput."""
    params = params or {}
    if not isinstance(params, dict):
        raise InvalidOperationInput("Parameters must be an object")
    known_params = {p.name: p for p in spec.params}
    unexpected = sorted(set(params) - set(known_params))
    if unexpected:
        raise InvalidOperationInput(
            f"Unexpected parameters for {spec.id}: {unexpected}"
        )
    missing = [name for name in spec.required_params if name not in params]
    if missing:
        raise InvalidOperationInput(f"Missing parameters for {spec.id}: {missing}")
    for name, value in params.items():
        _check_value(known_params[name], value, "Parameter")
        if value == "":
            raise InvalidOperationInput(f"Parameter '{name}' must not be empty")

    if body is None:
        if any(f.required for f in spec.body_fields):
            raise InvalidOperationInput(f"{spec.id} requires a request body")
        return dict(params), None
    if not spec.body_fields:
        raise InvalidOperationInput(f"{spec.id} does not accept a request body")
    if not isinstance(body, dict):
        raise InvalidOperationInput("Request body must be an object")
    _check_members(spec.body_fields, body, spec.id)
    return dict(params), copy.deepcopy(body)


def classify_risk(spec: OperationSpec, body: dict | None) -> str:
    """Return the operation's risk, raised by any sensitive body field it sets."""
    risk = spec.risk
    for field, value, escalated in spec.risk_escalations:
        if body and field in body and body[field] == value:
            if RISK_LEVELS.index(escalated) > RISK_LEVELS.index(risk):
                risk = escalated
    return risk
