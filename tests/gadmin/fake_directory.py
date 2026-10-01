"""In-memory stand-in for a googleapiclient Admin SDK Directory resource.

Only the methods the admin package calls are modelled. Every request is recorded,
and write methods are also recorded separately so tests can prove none happened.
Writes change the stored users, tokens, and group memberships like Google would.
"""

import json

import httplib2
from googleapiclient.errors import HttpError

WRITE_METHODS = {"insert", "update", "patch", "delete", "signOut", "makeAdmin"}
SUPER_ADMIN_ROLE_ID = "R_SUPER"


def http_error(status: int, reason: str = "error", *, code: str = "") -> HttpError:
    """Build an HttpError; ``code`` is Google's machine-readable error reason."""
    resp = httplib2.Response({"status": status})
    resp.reason = reason
    error = {"code": status, "message": reason}
    if code:
        error["errors"] = [{"reason": code, "message": reason}]
    content = json.dumps({"error": error}).encode()
    return HttpError(resp, content, uri="https://admin.googleapis.com/secret-uri")


class _Request:
    def __init__(self, run):
        self._run = run

    def execute(self):
        return self._run()


class _Resource:
    def __init__(self, directory, name):
        self._directory = directory
        self._name = name

    def __getattr__(self, method):
        def call(**kwargs):
            method_id = f"directory.{self._name}.{method}"
            self._directory.calls.append((method_id, kwargs))
            if method in WRITE_METHODS:
                self._directory.write_calls.append((method_id, kwargs))
            return _Request(lambda: self._directory.dispatch(method_id, kwargs))

        return call


class FakeDirectory:
    def __init__(self, customer_id: str = "C01", page_size: int = 1):
        self.customer_id = customer_id
        self.page_size = page_size
        self.users_by_key: dict[str, dict] = {}
        self.role_items = [
            {"roleId": "R_HELP", "roleName": "_HELP_DESK_ADMIN_ROLE"},
            {
                "roleId": SUPER_ADMIN_ROLE_ID,
                "roleName": "_SEED_ADMIN_ROLE",
                "isSuperAdminRole": True,
                "isSystemRole": True,
            },
        ]
        self.assignments: list[dict] = []
        # Group ID -> group with a "members" list of user IDs.
        self.group_items: dict[str, dict] = {}
        # User ID -> OAuth tokens the user granted.
        self.user_tokens: dict[str, list[dict]] = {}
        self.customer_record = {"id": customer_id, "phoneNumber": "+12025550000"}
        self.schema_items: list[dict] = []
        self.privilege_items = [{"serviceId": "S1", "privilegeName": "READ"}]
        self.domain_items = [
            {
                "domainName": "op.example",
                "isPrimary": True,
                "domainAliases": [{"domainAliasName": "op-alias.example"}],
            }
        ]
        # Organizational unit ID -> owning customer, and its path when not "/{id}".
        self.org_units: dict[str, str] = {}
        self.org_unit_paths: dict[str, str] = {}
        self.failures: dict[str, Exception] = {}
        self.calls: list[tuple[str, dict]] = []
        self.write_calls: list[tuple[str, dict]] = []
        self.closed = False

    def add_user(
        self,
        email: str,
        user_id: str,
        *,
        customer_id: str | None = None,
        super_admin: bool = False,
        delegated_admin: bool = False,
        suspended: bool = False,
        archived: bool = False,
        **extra,
    ) -> dict:
        user = {
            "id": user_id,
            "primaryEmail": email,
            "customerId": customer_id or self.customer_id,
            "isAdmin": super_admin,
            "isDelegatedAdmin": delegated_admin,
            "suspended": suspended,
            "archived": archived,
            **extra,
        }
        self.users_by_key[email] = self.users_by_key[user_id] = user
        if super_admin:
            self.assign(user_id, SUPER_ADMIN_ROLE_ID)
        return user

    def add_group(
        self,
        group_id: str,
        email: str,
        member_ids: list[str],
        customer_id: str | None = None,
    ) -> None:
        self.group_items[group_id] = {
            "id": group_id,
            "email": email,
            "customerId": customer_id or self.customer_id,
            "members": list(member_ids),
        }

    def add_org_unit(
        self, unit_id: str, customer_id: str | None = None, path: str | None = None
    ) -> None:
        self.org_units[unit_id] = customer_id or self.customer_id
        if path:
            self.org_unit_paths[unit_id] = path

    def assign(self, assigned_to: str, role_id: str, assignee_type: str = "user"):
        self.assignments.append(
            {
                "roleAssignmentId": f"A{len(self.assignments)}",
                "roleId": role_id,
                "assignedTo": assigned_to,
                "assigneeType": assignee_type,
                "scopeType": "CUSTOMER",
            }
        )

    def close(self):
        self.closed = True

    def users(self):
        return _Resource(self, "users")

    def roles(self):
        return _Resource(self, "roles")

    def roleAssignments(self):  # noqa: N802 - mirrors the Google client
        return _Resource(self, "roleAssignments")

    def groups(self):
        return _Resource(self, "groups")

    def members(self):
        return _Resource(self, "members")

    def tokens(self):
        return _Resource(self, "tokens")

    def domains(self):
        return _Resource(self, "domains")

    def orgunits(self):
        return _Resource(self, "orgunits")

    def customers(self):
        return _Resource(self, "customers")

    def schemas(self):
        return _Resource(self, "schemas")

    def privileges(self):
        return _Resource(self, "privileges")

    def dispatch(self, method_id: str, kwargs: dict):
        if method_id in self.failures:
            raise self.failures[method_id]
        if method_id == "directory.users.get":
            return dict(self._require_user(kwargs["userKey"]))
        if method_id == "directory.users.update":
            user = self._require_user(kwargs["userKey"])
            user.update(kwargs.get("body", {}))
            return dict(user)
        if method_id == "directory.users.delete":
            user = self._require_user(kwargs["userKey"])
            for key in (user["id"], user["primaryEmail"]):
                self.users_by_key.pop(key, None)
            return ""
        if method_id == "directory.users.signOut":
            self._require_user(kwargs["userKey"])
            return ""
        if method_id == "directory.customers.get":
            if kwargs["customerKey"] != self.customer_id:
                raise http_error(404)
            return dict(self.customer_record)
        if method_id == "directory.customers.patch":
            if kwargs["customerKey"] != self.customer_id:
                raise http_error(404)
            self.customer_record.update(kwargs["body"])
            return dict(self.customer_record)
        if method_id == "directory.privileges.list":
            self._require_customer(kwargs)
            return {"items": list(self.privilege_items)}
        if method_id == "directory.roles.list":
            return self._page(self.role_items, kwargs)
        if method_id == "directory.roles.insert":
            self._require_customer(kwargs)
            role = {
                "roleId": "R_NEW",
                "isSystemRole": False,
                "isSuperAdminRole": False,
                **kwargs["body"],
            }
            self.role_items.append(role)
            return dict(role)
        if method_id == "directory.roles.patch":
            role = self._require_custom_role(kwargs)
            role.update(kwargs["body"])
            return dict(role)
        if method_id == "directory.roles.delete":
            role = self._require_custom_role(kwargs)
            self.role_items.remove(role)
            return {}
        if method_id == "directory.schemas.get":
            if kwargs["customerId"] != self.customer_id:
                raise http_error(404)
            return dict(self._require_schema(kwargs["schemaKey"]))
        if method_id == "directory.schemas.insert":
            if kwargs["customerId"] != self.customer_id:
                raise http_error(404)
            schema = {
                "schemaId": "S_NEW",
                "displayName": kwargs["body"]["schemaName"],
                **kwargs["body"],
            }
            self.schema_items.append(schema)
            return dict(schema)
        if method_id == "directory.schemas.patch":
            schema = self._require_schema(kwargs["schemaKey"])
            schema.update(kwargs["body"])
            return dict(schema)
        if method_id == "directory.schemas.delete":
            schema = self._require_schema(kwargs["schemaKey"])
            self.schema_items.remove(schema)
            return {}
        if method_id == "directory.roles.get":
            self._require_customer(kwargs)
            for role in self.role_items:
                if role["roleId"] == kwargs["roleId"]:
                    return dict(role)
            raise http_error(404, "Resource Not Found: roleId")
        if method_id == "directory.roleAssignments.get":
            self._require_customer(kwargs)
            for assignment in self.assignments:
                if assignment["roleAssignmentId"] == kwargs["roleAssignmentId"]:
                    return dict(assignment)
            raise http_error(404, "Resource Not Found: roleAssignmentId")
        if method_id == "directory.domains.list":
            self._require_customer(kwargs)
            return {"domains": [dict(d) for d in self.domain_items]}
        if method_id == "directory.orgunits.get":
            if kwargs["customerId"] != self.customer_id:
                raise http_error(403, "Not Authorized to access this resource/api")
            unit = kwargs["orgUnitPath"].removeprefix("id:")
            if self.org_units.get(unit) != self.customer_id:
                raise http_error(404, "Org unit not found")
            path = self.org_unit_paths.get(unit, f"/{unit}")
            return {"orgUnitId": f"id:{unit}", "orgUnitPath": path}
        if method_id == "directory.groups.get":
            group = self._require_group(kwargs["groupKey"])
            return {
                k: v for k, v in group.items() if k not in ("members", "customerId")
            }
        if method_id == "directory.members.get":
            group = self._require_group(kwargs["groupKey"])
            user = self._find_user(kwargs["memberKey"])
            if user is None or user["id"] not in group["members"]:
                raise http_error(404, "Resource Not Found: memberKey")
            return self._member(user)
        if method_id == "directory.roleAssignments.list":
            items = [
                a
                for a in self.assignments
                if kwargs.get("roleId") in (None, a["roleId"])
                and kwargs.get("userKey") in (None, a["assignedTo"])
            ]
            return self._page(items, kwargs)
        if method_id == "directory.tokens.list":
            user = self._require_user(kwargs["userKey"])
            return {"items": list(self.user_tokens.get(user["id"], []))}
        if method_id == "directory.tokens.delete":
            user = self._require_user(kwargs["userKey"])
            tokens = self.user_tokens.get(user["id"], [])
            self.user_tokens[user["id"]] = [
                t for t in tokens if t["clientId"] != kwargs["clientId"]
            ]
            return ""
        if method_id == "directory.groups.list":
            user = (
                self._require_user(kwargs["userKey"]) if kwargs.get("userKey") else None
            )
            groups = [
                {k: v for k, v in group.items() if k != "members"}
                for group in self.group_items.values()
                if (user is None or user["id"] in group["members"])
                and (
                    not kwargs.get("customer")
                    or group["customerId"] == kwargs["customer"]
                )
            ]
            return self._page(groups, kwargs, items_key="groups")
        if method_id == "directory.members.list":
            group = self.group_items.get(kwargs["groupKey"])
            if group is None:
                raise http_error(404, "Resource Not Found: groupKey")
            members = [
                self._member(self._require_user(user_id))
                for user_id in group["members"]
            ]
            return self._page(members, kwargs, items_key="members")
        if method_id == "directory.members.delete":
            group = self.group_items.get(kwargs["groupKey"])
            user = self._find_user(kwargs["memberKey"])
            if group is None or user is None or user["id"] not in group["members"]:
                raise http_error(404, "Resource Not Found: memberKey")
            group["members"].remove(user["id"])
            return ""
        if method_id == "directory.members.insert":
            group = self._require_group(kwargs["groupKey"])
            user = self._require_user(kwargs["body"]["email"])
            group["members"].append(user["id"])
            return {**self._member(user), "role": kwargs["body"].get("role", "MEMBER")}
        raise AssertionError(f"FakeDirectory does not model {method_id}")

    def _require_custom_role(self, kwargs: dict) -> dict:
        self._require_customer(kwargs)
        for role in self.role_items:
            if role["roleId"] == kwargs["roleId"]:
                return role
        raise http_error(404, "Role not found")

    def _require_schema(self, schema_id: str) -> dict:
        for schema in self.schema_items:
            if schema["schemaId"] == schema_id:
                return schema
        raise http_error(404, "Schema not found")

    def _member(self, user: dict) -> dict:
        return {
            "id": user["id"],
            "email": user["primaryEmail"],
            "role": "MEMBER",
            "type": "USER",
            "status": "ACTIVE",
        }

    def _require_customer(self, kwargs: dict) -> None:
        if kwargs.get("customer") != self.customer_id:
            raise http_error(403, "Not Authorized to access this resource/api")

    def _require_group(self, key: str) -> dict:
        for group in self.group_items.values():
            if key in (group["id"], group["email"]) or (
                key.casefold() == group["email"].casefold()
            ):
                return group
        raise http_error(404, "Resource Not Found: groupKey")

    def _find_user(self, key: str) -> dict | None:
        # Directory resolves emails case-insensitively.
        for user_key, user in self.users_by_key.items():
            if user_key.casefold() == key.casefold():
                return user
        return None

    def _require_user(self, key: str) -> dict:
        user = self._find_user(key)
        if user is None:
            raise http_error(404, "Resource Not Found: userKey")
        return user

    def _page(self, items: list[dict], kwargs: dict, items_key: str = "items") -> dict:
        start = int(kwargs.get("pageToken") or 0)
        end = start + self.page_size
        page = {"kind": "admin#directory#list", items_key: items[start:end]}
        if end < len(items):
            page["nextPageToken"] = str(end)
        return page
