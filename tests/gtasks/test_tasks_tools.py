"""Unit tests for gtasks/tasks_tools.py using mocked Google API services."""

from typing import Any, Callable, Iterator
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError

from auth.permissions import set_permissions
from core.utils import UserInputError
from gtasks.tasks_tools import (
    StructuredTask,
    _adjust_due_max_for_tasks_api,
    _create_task_impl,
    _move_task_impl,
    _update_task_impl,
    _validate_rfc3339_date,
    get_structured_tasks,
    get_task,
    get_task_list,
    list_task_lists,
    list_tasks,
    manage_task,
    manage_task_list,
    serialize_tasks,
)


def _unwrap(tool: Any) -> Callable[..., Any]:
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _without_auth(tool: Any) -> Callable[..., Any]:
    """Strip only the auth decorator, keeping handle_http_errors in place."""
    return (tool.fn if hasattr(tool, "fn") else tool).__wrapped__


@pytest.fixture(autouse=True)
def _reset_permissions_state() -> Iterator[None]:
    set_permissions(None)
    yield
    set_permissions(None)


@pytest.mark.parametrize(
    ("due_max", "expected"),
    [
        ("2026-05-20T00:00:00Z", "2026-05-21T00:00:00Z"),
        ("2026-05-20T00:00:00+05:00", "2026-05-21T00:00:00+05:00"),
        ("2026-05-20T00:00:00", "2026-05-21T00:00:00Z"),
        ("2026-12-31T00:00:00Z", "2027-01-01T00:00:00Z"),
        ("not-a-date", "not-a-date"),
    ],
)
def test_adjust_due_max(due_max: str, expected: str) -> None:
    assert _adjust_due_max_for_tasks_api(due_max) == expected


class TestValidateRfc3339Date:
    @pytest.mark.parametrize(
        "due", ["2026-05-20T00:00:00Z", "2026-05-20T00:00:00+05:30"]
    )
    def test_accepts_offset_aware_datetime(self, due: str) -> None:
        _validate_rfc3339_date(due)

    @pytest.mark.parametrize(
        "due",
        [
            "2026-05-20",
            "2026-05-20 00:00:00Z",
            "garbageTgarbage",
            "2026-05-20T00:00:00",
        ],
    )
    def test_rejects(self, due: str) -> None:
        with pytest.raises(UserInputError, match="Invalid due date format"):
            _validate_rfc3339_date(due)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        (list_task_lists, {}),
        (get_task_list, {"task_list_id": "tl1"}),
        (list_tasks, {"task_list_id": "tl1"}),
        (get_task, {"task_list_id": "tl1", "task_id": "t1"}),
    ],
    ids=["list_task_lists", "get_task_list", "list_tasks", "get_task"],
)
async def test_read_tools_surface_rate_limit_hint(
    tool: Any, kwargs: dict[str, str]
) -> None:
    error = HttpError(
        Mock(status=403, reason="Forbidden"),
        b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}',
    )
    service = Mock()
    for resource in (service.tasklists(), service.tasks()):
        resource.list().execute.side_effect = error
        resource.get().execute.side_effect = error
    with pytest.raises(ToolError) as excinfo:
        await _without_auth(tool)(
            service=service, user_google_email="u@e.com", **kwargs
        )
    assert "rate limit or quota exceeded" in str(excinfo.value)
    assert "re-authenticate" not in str(excinfo.value)


class TestGetStructuredTasks:
    def test_sorts_top_level_by_position(self) -> None:
        tasks = [
            {"id": "b", "title": "B", "position": "00000000000000000002"},
            {"id": "a", "title": "A", "position": "00000000000000000001"},
        ]
        assert [t.id for t in get_structured_tasks(tasks)] == ["a", "b"]

    def test_child_listed_before_parent(self) -> None:
        tasks = [
            {"id": "2", "title": "Child", "position": "0", "parent": "1"},
            {"id": "1", "title": "Parent", "position": "0"},
        ]
        result = get_structured_tasks(tasks)
        assert [t.id for t in result] == ["1"]
        assert result[0].is_placeholder_parent is False
        assert [t.id for t in result[0].subtasks] == ["2"]

    def test_orphaned_subtask_creates_placeholder(self) -> None:
        tasks = [{"id": "2", "title": "Orphan", "position": "0", "parent": "missing"}]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert result[0].id == "missing"
        assert result[0].is_placeholder_parent is True
        assert [t.id for t in result[0].subtasks] == ["2"]

    def test_orphans_share_one_placeholder(self) -> None:
        tasks = [
            {"id": "c1", "position": "0", "parent": "missing"},
            {"id": "c2", "position": "1", "parent": "missing"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert [t.id for t in result[0].subtasks] == ["c1", "c2"]

    def test_empty_input(self) -> None:
        assert get_structured_tasks([]) == []

    def test_sorts_children_by_position(self) -> None:
        tasks = [
            {"id": "p", "title": "Parent", "position": "0"},
            {"id": "c2", "title": "Child 2", "position": "1", "parent": "p"},
            {"id": "c1", "title": "Child 1", "position": "0", "parent": "p"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert [t.id for t in result[0].subtasks] == ["c1", "c2"]

    def test_tasks_without_position_sort_last(self) -> None:
        tasks = [
            {"id": "1", "title": "No position"},
            {"id": "2", "title": "Has position", "position": "5"},
        ]
        assert [t.id for t in get_structured_tasks(tasks)] == ["2", "1"]


def _task(**fields: str) -> StructuredTask:
    fields.setdefault("status", "needsAction")
    fields.setdefault("updated", "2026-01-01")
    return StructuredTask(fields, is_placeholder_parent=False)


class TestSerializeTasks:
    def test_basic_serialization(self) -> None:
        result = serialize_tasks([_task(id="1", title="Task A")], 0)
        assert result == (
            "- Task A (ID: 1)\n  Status: needsAction\n  Updated: 2026-01-01\n\n"
        )

    def test_missing_status_and_updated_show_na(self) -> None:
        task = StructuredTask({"id": "1", "title": "T"}, is_placeholder_parent=False)
        result = serialize_tasks([task], 0)
        assert "  Status: N/A\n" in result
        assert "  Updated: N/A\n" in result

    def test_indentation_for_subtasks(self) -> None:
        parent = _task(id="p1", title="Parent")
        child = _task(id="c1", title="Sub")
        grandchild = _task(id="g1", title="Deep")
        parent.add_subtask(child)
        child.add_subtask(grandchild)
        result = serialize_tasks([parent], 0)
        assert "\n  * Sub (ID: c1)\n    Status: needsAction\n" in result
        assert "\n    * Deep (ID: g1)\n      Status: needsAction\n" in result

    def test_placeholder_note(self) -> None:
        placeholder = StructuredTask({"id": "orphan"}, is_placeholder_parent=True)
        placeholder.add_subtask(_task(id="c1", title="Real"))
        result = serialize_tasks([placeholder], 0)
        assert "- Unknown parent (ID: orphan)" in result
        assert "1 tasks with title Unknown parent are included as placeholders" in (
            result
        )

    def test_truncates_long_notes(self) -> None:
        result = serialize_tasks([_task(id="1", title="T", notes="x" * 200)], 0)
        assert f"  Notes: {'x' * 100}...\n" in result

    def test_notes_at_exactly_100_chars_no_ellipsis(self) -> None:
        result = serialize_tasks([_task(id="1", title="T", notes="y" * 100)], 0)
        assert f"  Notes: {'y' * 100}\n" in result

    def test_untitled_task_shows_untitled(self) -> None:
        assert "- Untitled (ID: 1)" in serialize_tasks([_task(id="1")], 0)

    def test_due_date_appears_when_present(self) -> None:
        task = _task(id="1", title="T", due="2026-06-01T00:00:00Z")
        assert "  Due: 2026-06-01T00:00:00Z\n" in serialize_tasks([task], 0)

    def test_completed_appears_when_present(self) -> None:
        task = _task(
            id="1", title="T", status="completed", completed="2026-05-01T10:00:00Z"
        )
        assert "  Completed: 2026-05-01T10:00:00Z\n" in serialize_tasks([task], 0)


class TestCreateTaskImpl:
    @pytest.mark.asyncio
    async def test_no_optional_params_by_default(self) -> None:
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {
            "id": "t1",
            "title": "Simple",
            "status": "needsAction",
        }
        result = await _create_task_impl(
            mock_service, "user@example.com", "tl123", "Simple"
        )
        mock_service.tasks().insert.assert_called_with(
            tasklist="tl123", body={"title": "Simple"}
        )
        assert "- Title: Simple" in result
        assert "- ID: t1" in result
        assert "Due Date" not in result

    @pytest.mark.asyncio
    async def test_renders_due_notes_and_link(self) -> None:
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {
            "id": "t3",
            "title": "With due",
            "due": "2026-05-20T00:00:00Z",
            "notes": "Some notes",
            "webViewLink": "https://tasks.google.com/t3",
        }
        result = await _create_task_impl(
            mock_service,
            "user@example.com",
            "tl123",
            "With due",
            notes="Some notes",
            due="2026-05-20T00:00:00Z",
        )
        assert "- Due Date: 2026-05-20T00:00:00Z" in result
        assert "- Notes: Some notes" in result
        assert "- Web View Link: https://tasks.google.com/t3" in result


class TestUpdateTaskImpl:
    @pytest.mark.asyncio
    async def test_updates_title_and_status(self) -> None:
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {
            "id": "t1",
            "title": "Old",
            "status": "needsAction",
        }
        mock_service.tasks().update().execute.return_value = {
            "id": "t1",
            "title": "New",
            "status": "completed",
            "completed": "2026-01-02T00:00:00Z",
        }
        result = await _update_task_impl(
            mock_service,
            "user@example.com",
            "tl123",
            "t1",
            title="New",
            status="completed",
        )
        body = mock_service.tasks().update.call_args.kwargs["body"]
        assert body["title"] == "New"
        assert body["status"] == "completed"
        assert "- Title: New" in result
        assert "- Completed: 2026-01-02T00:00:00Z" in result

    @pytest.mark.asyncio
    async def test_preserves_existing_fields(self) -> None:
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {
            "id": "t1",
            "title": "Original",
            "status": "completed",
            "notes": "Keep me",
            "due": "2026-06-01T00:00:00.000Z",
        }
        mock_service.tasks().update().execute.return_value = {
            "id": "t1",
            "title": "Original",
        }
        await _update_task_impl(mock_service, "user@example.com", "tl123", "t1")
        assert mock_service.tasks().update.call_args.kwargs["body"] == {
            "id": "t1",
            "title": "Original",
            "status": "completed",
            "notes": "Keep me",
            "due": "2026-06-01T00:00:00.000Z",
        }

    @pytest.mark.asyncio
    async def test_defaults_when_current_task_is_sparse(self) -> None:
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {"id": "t1"}
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": ""}
        await _update_task_impl(mock_service, "user@example.com", "tl123", "t1")
        mock_service.tasks().get.assert_called_with(tasklist="tl123", task="t1")
        mock_service.tasks().update.assert_called_with(
            tasklist="tl123",
            task="t1",
            body={"id": "t1", "title": "", "status": "needsAction"},
        )

    @pytest.mark.asyncio
    async def test_overrides_notes_and_due(self) -> None:
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {
            "id": "t1",
            "title": "T",
            "notes": "Old note",
            "due": "2026-01-01T00:00:00.000Z",
        }
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": "T"}
        await _update_task_impl(
            mock_service,
            "user@example.com",
            "tl123",
            "t1",
            notes="New note",
            due="2026-02-01T00:00:00Z",
        )
        body = mock_service.tasks().update.call_args.kwargs["body"]
        assert body["notes"] == "New note"
        assert body["due"] == "2026-02-01T00:00:00Z"


class TestMoveTaskImpl:
    @pytest.mark.asyncio
    async def test_moves_under_parent_after_sibling(self) -> None:
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {
            "id": "t1",
            "title": "Moved task",
            "parent": "new_parent",
            "position": "12345",
        }
        result = await _move_task_impl(
            mock_service,
            "user@example.com",
            "tl123",
            "t1",
            parent="new_parent",
            previous="sibling",
        )
        mock_service.tasks().move.assert_called_with(
            tasklist="tl123", task="t1", parent="new_parent", previous="sibling"
        )
        assert "- Parent Task ID: new_parent" in result
        assert "- Position: 12345" in result
        assert (
            "- Move Details: made a subtask of new_parent, positioned after sibling"
            in result
        )

    @pytest.mark.asyncio
    async def test_omits_optional_params_by_default(self) -> None:
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {"id": "t1", "title": "T"}
        result = await _move_task_impl(mock_service, "user@example.com", "tl123", "t1")
        mock_service.tasks().move.assert_called_with(tasklist="tl123", task="t1")
        assert "Move Details" not in result


def _list_service(*pages: Any) -> Mock:
    """Mock service whose tasks().list().execute returns each page in turn."""
    mock_service = Mock()
    mock_service.tasks.return_value.list.return_value.execute.side_effect = list(pages)
    return mock_service


def _list_calls(mock_service: Mock) -> list[dict[str, Any]]:
    return [c.kwargs for c in mock_service.tasks.return_value.list.call_args_list]


class TestListTasks:
    @pytest.mark.asyncio
    async def test_no_tasks(self) -> None:
        mock_service = _list_service({"items": []})
        result = await _unwrap(list_tasks)(
            service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
        )
        assert result == "No tasks found in task list tl1 for u@e.com."

    @pytest.mark.asyncio
    async def test_single_page_renders_structured_tasks(self) -> None:
        mock_service = _list_service(
            {
                "items": [
                    {"id": "c", "title": "Child", "parent": "p", "position": "0"},
                    {"id": "p", "title": "Parent", "position": "0"},
                ]
            }
        )
        result = await _unwrap(list_tasks)(
            service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
        )
        assert result.startswith("Tasks in list tl1 for u@e.com:\n- Parent (ID: p)")
        assert "\n  * Child (ID: c)" in result
        assert len(_list_calls(mock_service)) == 1
        assert "Next page token" not in result

    @pytest.mark.asyncio
    async def test_fetches_more_pages_until_max_results(self) -> None:
        mock_service = _list_service(
            {"items": [{"id": "1"}, {"id": "2"}], "nextPageToken": "tok1"},
            {"items": [{"id": "3"}], "nextPageToken": "tok2"},
        )
        result = await _unwrap(list_tasks)(
            service=mock_service,
            user_google_email="u@e.com",
            task_list_id="tl1",
            max_results=3,
        )
        calls = _list_calls(mock_service)
        assert len(calls) == 2
        assert calls[0]["maxResults"] == 3
        assert calls[1]["pageToken"] == "tok1"
        assert int(calls[1]["maxResults"]) == 1
        assert all(f"(ID: {i})" in result for i in ("1", "2", "3"))
        assert "Next page token: tok2" in result

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self) -> None:
        mock_service = _list_service(
            {"items": [{"id": "1"}], "nextPageToken": "tok1"},
            {"items": [], "nextPageToken": "tok2"},
        )
        await _unwrap(list_tasks)(
            service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
        )
        assert len(_list_calls(mock_service)) == 2

    @pytest.mark.asyncio
    async def test_caps_max_results(self) -> None:
        mock_service = _list_service(
            {"items": [{"id": "1"}], "nextPageToken": "tok1"},
            {"items": [{"id": "2"}]},
        )
        await _unwrap(list_tasks)(
            service=mock_service,
            user_google_email="u@e.com",
            task_list_id="tl1",
            max_results=50_000,
        )
        assert int(_list_calls(mock_service)[1]["maxResults"]) == 9_999

    @pytest.mark.asyncio
    async def test_default_params(self) -> None:
        mock_service = _list_service({"items": []})
        await _unwrap(list_tasks)(
            service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
        )
        assert _list_calls(mock_service)[0] == {
            "tasklist": "tl1",
            "maxResults": 20,
            "showCompleted": True,
            "showDeleted": False,
            "showHidden": False,
            "showAssigned": False,
        }

    @pytest.mark.asyncio
    async def test_forwards_filters_and_adjusts_due_max(self) -> None:
        mock_service = _list_service({"items": []})
        await _unwrap(list_tasks)(
            service=mock_service,
            user_google_email="u@e.com",
            task_list_id="tl1",
            page_token="tok0",
            due_max="2026-05-20T00:00:00Z",
            due_min="2026-05-01T00:00:00Z",
            completed_min="2026-04-01T00:00:00Z",
            completed_max="2026-04-30T00:00:00Z",
            updated_min="2026-03-01T00:00:00Z",
        )
        params = _list_calls(mock_service)[0]
        assert params["pageToken"] == "tok0"
        assert params["dueMax"] == "2026-05-21T00:00:00Z"
        assert params["dueMin"] == "2026-05-01T00:00:00Z"
        assert params["completedMin"] == "2026-04-01T00:00:00Z"
        assert params["completedMax"] == "2026-04-30T00:00:00Z"
        assert params["updatedMin"] == "2026-03-01T00:00:00Z"


def _manage_task_service() -> Mock:
    mock_service = Mock()
    task = {"id": "t1", "title": "Task", "status": "needsAction"}
    mock_service.tasks().insert().execute.return_value = task
    mock_service.tasks().get().execute.return_value = task
    mock_service.tasks().update().execute.return_value = task
    mock_service.tasks().move().execute.return_value = task
    return mock_service


async def _manage_task(mock_service: Mock, **kwargs: Any) -> str:
    return await _unwrap(manage_task)(
        service=mock_service, user_google_email="u@e.com", task_list_id="tl1", **kwargs
    )


class TestManageTaskDispatcher:
    @pytest.mark.asyncio
    async def test_invalid_action_raises(self) -> None:
        mock_service = Mock()
        with pytest.raises(UserInputError, match="Invalid action 'fly'"):
            await _manage_task(mock_service, action="fly")
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_create_without_title_raises(self) -> None:
        with pytest.raises(UserInputError, match="'title' is required"):
            await _manage_task(Mock(), action="create")

    @pytest.mark.asyncio
    async def test_create_with_status_raises(self) -> None:
        with pytest.raises(UserInputError, match="'status' is only supported"):
            await _manage_task(Mock(), action="create", title="T", status="completed")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["update", "delete", "move"])
    async def test_task_id_required(self, action: str) -> None:
        with pytest.raises(UserInputError, match="'task_id' is required"):
            await _manage_task(Mock(), action=action)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["update", "delete", "move"])
    async def test_invalid_status_raises(self, action: str) -> None:
        mock_service = Mock()
        with pytest.raises(UserInputError, match="invalid status"):
            await _manage_task(mock_service, action=action, task_id="t1", status="done")
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["create", "update"])
    async def test_invalid_due_raises(self, action: str) -> None:
        mock_service = Mock()
        with pytest.raises(UserInputError, match="Invalid due date format"):
            await _manage_task(
                mock_service, action=action, title="T", task_id="t1", due="2026-05-20"
            )
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_create_routes_to_impl(self) -> None:
        mock_service = _manage_task_service()
        result = await _manage_task(
            mock_service,
            action="create",
            title="Task",
            notes="n",
            due="2026-05-20T00:00:00Z",
            parent="p1",
            previous="s1",
        )
        assert result.startswith("Task Created for u@e.com:")
        mock_service.tasks().insert.assert_called_with(
            tasklist="tl1",
            body={"title": "Task", "notes": "n", "due": "2026-05-20T00:00:00Z"},
            parent="p1",
            previous="s1",
        )

    @pytest.mark.asyncio
    async def test_update_routes_to_impl(self) -> None:
        mock_service = _manage_task_service()
        result = await _manage_task(
            mock_service, action="update", task_id="t1", status="completed"
        )
        assert result.startswith("Task Updated for u@e.com:")
        body = mock_service.tasks().update.call_args.kwargs["body"]
        assert body["status"] == "completed"

    @pytest.mark.asyncio
    async def test_delete_routes_to_impl(self) -> None:
        mock_service = _manage_task_service()
        result = await _manage_task(mock_service, action="delete", task_id="t1")
        assert "Task t1 has been deleted from task list tl1" in result
        mock_service.tasks().delete.assert_called_with(tasklist="tl1", task="t1")

    @pytest.mark.asyncio
    async def test_move_routes_to_impl(self) -> None:
        mock_service = _manage_task_service()
        result = await _manage_task(
            mock_service, action="move", task_id="t1", destination_task_list="tl2"
        )
        assert result.startswith("Task Moved for u@e.com:")
        assert "- Move Details: moved to task list tl2" in result
        mock_service.tasks().move.assert_called_with(
            tasklist="tl1", task="t1", destinationTasklist="tl2"
        )

    @pytest.mark.asyncio
    async def test_denied_action_raises(self) -> None:
        set_permissions({"tasks": "manage"})
        mock_service = Mock()
        with pytest.raises(UserInputError, match="'delete' action is not allowed"):
            await _manage_task(mock_service, action="delete", task_id="t1")
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_allowed_action_under_manage_level(self) -> None:
        set_permissions({"tasks": "manage"})
        result = await _manage_task(
            _manage_task_service(), action="create", title="Task"
        )
        assert result.startswith("Task Created for u@e.com:")


def _manage_task_list_service() -> Mock:
    mock_service = Mock()
    mock_service.tasklists().insert().execute.return_value = {
        "id": "tl1",
        "title": "List",
    }
    mock_service.tasklists().update().execute.return_value = {
        "id": "tl1",
        "title": "Renamed",
    }
    return mock_service


async def _manage_task_list(mock_service: Mock, **kwargs: Any) -> str:
    return await _unwrap(manage_task_list)(
        service=mock_service, user_google_email="u@e.com", **kwargs
    )


class TestManageTaskListDispatcher:
    @pytest.mark.asyncio
    async def test_invalid_action_raises(self) -> None:
        mock_service = Mock()
        with pytest.raises(UserInputError, match="Invalid action 'archive'"):
            await _manage_task_list(mock_service, action="archive")
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_create_without_title_raises(self) -> None:
        with pytest.raises(UserInputError, match="'title' is required"):
            await _manage_task_list(Mock(), action="create")

    @pytest.mark.asyncio
    async def test_update_without_task_list_id_raises(self) -> None:
        with pytest.raises(UserInputError, match="'task_list_id' is required"):
            await _manage_task_list(Mock(), action="update", title="New")

    @pytest.mark.asyncio
    async def test_update_without_title_raises(self) -> None:
        with pytest.raises(UserInputError, match="'title' is required"):
            await _manage_task_list(Mock(), action="update", task_list_id="tl1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["delete", "clear_completed"])
    async def test_task_list_id_required(self, action: str) -> None:
        with pytest.raises(UserInputError, match="'task_list_id' is required"):
            await _manage_task_list(Mock(), action=action)

    @pytest.mark.asyncio
    async def test_create_routes_to_impl(self) -> None:
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(mock_service, action="create", title="List")
        assert result.startswith("Task List Created for u@e.com:")
        assert "- ID: tl1" in result
        mock_service.tasklists().insert.assert_called_with(body={"title": "List"})

    @pytest.mark.asyncio
    async def test_update_routes_to_impl(self) -> None:
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(
            mock_service, action="update", task_list_id="tl1", title="Renamed"
        )
        assert result.startswith("Task List Updated for u@e.com:")
        assert "- Title: Renamed" in result
        mock_service.tasklists().update.assert_called_with(
            tasklist="tl1", body={"id": "tl1", "title": "Renamed"}
        )

    @pytest.mark.asyncio
    async def test_delete_routes_to_impl(self) -> None:
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(
            mock_service, action="delete", task_list_id="tl1"
        )
        assert "Task list tl1 has been deleted" in result
        mock_service.tasklists().delete.assert_called_with(tasklist="tl1")

    @pytest.mark.asyncio
    async def test_clear_completed_routes_to_impl(self) -> None:
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(
            mock_service, action="clear_completed", task_list_id="tl1"
        )
        assert "cleared from task list tl1" in result
        mock_service.tasks().clear.assert_called_with(tasklist="tl1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["delete", "clear_completed"])
    async def test_denied_action_raises(self, action: str) -> None:
        set_permissions({"tasks": "manage"})
        mock_service = Mock()
        with pytest.raises(UserInputError, match="is not allowed"):
            await _manage_task_list(mock_service, action=action, task_list_id="tl1")
        assert not mock_service.mock_calls
