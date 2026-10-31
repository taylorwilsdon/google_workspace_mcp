"""
Unit tests for gtasks/tasks_tools.py.

Validates the task structuring and serialization helpers, the _impl functions,
list_tasks pagination, and the manage_task / manage_task_list dispatchers using
mocked Google API service objects. No credentials or network access required.
"""

from unittest.mock import Mock, patch

import pytest
from googleapiclient.errors import HttpError

from auth.permissions import set_permissions
from core.utils import UserInputError
from gtasks.tasks_tools import (
    StructuredTask,
    _adjust_due_max_for_tasks_api,
    _clear_completed_tasks_impl,
    _create_task_impl,
    _create_task_list_impl,
    _delete_task_impl,
    _delete_task_list_impl,
    _format_reauth_message,
    _move_task_impl,
    _update_task_impl,
    _update_task_list_impl,
    _validate_rfc3339_date,
    get_structured_tasks,
    list_tasks,
    manage_task,
    manage_task_list,
    serialize_tasks,
    sort_structured_tasks,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _http_error(status):
    return HttpError(Mock(status=status, reason="error"), b"")


@pytest.fixture(autouse=True)
def _reset_permissions_state():
    """Ensure each test starts and ends with no active permissions."""
    set_permissions(None)
    yield
    set_permissions(None)


# --- Helper/utility tests ---


class TestAdjustDueMax:
    def test_bumps_utc_by_one_day(self):
        # Verify day is incremented by one for a UTC Z-suffix timestamp
        assert (
            _adjust_due_max_for_tasks_api("2026-05-20T00:00:00Z")
            == "2026-05-21T00:00:00Z"
        )

    def test_preserves_timezone(self):
        # Non-UTC offsets should survive the bump with their offset intact
        result = _adjust_due_max_for_tasks_api("2026-05-20T00:00:00+05:00")
        assert result == "2026-05-21T00:00:00+05:00"

    def test_naive_datetime_treated_as_utc(self):
        # A timestamp without an offset is assumed to be UTC and gets the Z suffix
        assert (
            _adjust_due_max_for_tasks_api("2026-05-20T00:00:00")
            == "2026-05-21T00:00:00Z"
        )

    def test_handles_invalid_input(self):
        # Unparseable strings must be returned unmodified rather than raising
        assert _adjust_due_max_for_tasks_api("not-a-date") == "not-a-date"

    def test_utc_output_ends_with_z(self):
        # UTC-based results must use Z suffix, not +00:00
        result = _adjust_due_max_for_tasks_api("2026-12-31T00:00:00Z")
        assert result == "2027-01-01T00:00:00Z"
        assert "+00:00" not in result

    def test_bumps_across_month_boundary(self):
        # Month rollover (Jan 31 -> Feb 1) must be handled correctly
        assert (
            _adjust_due_max_for_tasks_api("2026-01-31T00:00:00Z")
            == "2026-02-01T00:00:00Z"
        )


class TestValidateRfc3339Date:
    def test_valid_rfc3339(self):
        # A well-formed RFC 3339 timestamp should pass without raising
        _validate_rfc3339_date("2026-05-20T00:00:00Z")

    def test_valid_with_offset(self):
        # Offset-aware timestamps other than Z must also be accepted
        _validate_rfc3339_date("2026-05-20T00:00:00+05:30")

    def test_rejects_date_only(self):
        # Date-only strings are rejected because the Tasks API requires full datetimes
        with pytest.raises(UserInputError, match="Invalid due date format"):
            _validate_rfc3339_date("2026-05-20")

    def test_rejects_invalid_string(self):
        # Malformed strings containing a T must still raise
        with pytest.raises(UserInputError, match="Invalid due date format"):
            _validate_rfc3339_date("garbageTgarbage")

    def test_rejects_naive_datetime(self):
        # Datetimes without timezone info are rejected by the Tasks API
        with pytest.raises(UserInputError, match="Invalid due date format"):
            _validate_rfc3339_date("2026-05-20T00:00:00")


class TestFormatReauthMessage:
    def test_non_auth_error_has_no_hint(self):
        # Errors other than 401/403 should not suggest re-authenticating
        result = _format_reauth_message(_http_error(500), "u@e.com")
        assert result.startswith("API error:")
        assert "re-authenticate" not in result

    def test_non_http_error_has_no_hint(self):
        # Plain exceptions should be reported without a re-auth hint
        result = _format_reauth_message(ValueError("boom"), "u@e.com")
        assert result == "API error: boom"

    @patch("gtasks.tasks_tools.is_oauth21_enabled", return_value=False)
    def test_legacy_oauth_hint(self, _mock_oauth21):
        # Without OAuth 2.1 the hint should point at start_google_auth with the email
        result = _format_reauth_message(_http_error(401), "u@e.com")
        assert "re-authenticate" in result
        assert "start_google_auth" in result
        assert "u@e.com" in result

    @patch("gtasks.tasks_tools.is_external_oauth21_provider", return_value=False)
    @patch("gtasks.tasks_tools.is_oauth21_enabled", return_value=True)
    def test_oauth21_hint(self, _mock_oauth21, _mock_external):
        # With OAuth 2.1 the hint should point at the MCP client's OAuth flow
        result = _format_reauth_message(_http_error(403), "u@e.com")
        assert "MCP client's OAuth 2.1 flow" in result
        assert "start_google_auth" not in result

    @patch("gtasks.tasks_tools.is_external_oauth21_provider", return_value=True)
    @patch("gtasks.tasks_tools.is_oauth21_enabled", return_value=True)
    def test_external_provider_hint(self, _mock_oauth21, _mock_external):
        # With an external OAuth 2.1 provider the hint should ask for a bearer token
        result = _format_reauth_message(_http_error(401), "u@e.com")
        assert "bearer token" in result


# --- StructuredTask tests ---


class TestStructuredTask:
    def test_creates_task_with_subtasks(self):
        # add_subtask should append to the subtasks list
        parent = StructuredTask(
            {"id": "p1", "title": "Parent"}, is_placeholder_parent=False
        )
        child = StructuredTask(
            {"id": "c1", "title": "Child"}, is_placeholder_parent=False
        )
        parent.add_subtask(child)
        assert parent.subtasks == [child]

    def test_placeholder_parent(self):
        # is_placeholder_parent flag must be stored faithfully
        task = StructuredTask({"id": "orphan"}, is_placeholder_parent=True)
        assert task.is_placeholder_parent is True

    def test_repr(self):
        # repr should include the task title and subtask count
        task = StructuredTask({"id": "1", "title": "Hi"}, is_placeholder_parent=False)
        assert repr(task) == "StructuredTask(title=Hi, 0 subtasks)"

    def test_optional_fields_default_to_none(self):
        # Fields absent from the dict should not raise and should be None
        task = StructuredTask({"id": "x"}, is_placeholder_parent=False)
        assert task.title is None
        assert task.status is None
        assert task.due is None
        assert task.notes is None
        assert task.updated is None
        assert task.completed is None

    def test_multiple_subtasks_preserve_order(self):
        # Subtasks must be stored in insertion order
        parent = StructuredTask({"id": "p"}, is_placeholder_parent=False)
        for i in range(3):
            parent.add_subtask(
                StructuredTask({"id": str(i)}, is_placeholder_parent=False)
            )
        assert [s.id for s in parent.subtasks] == ["0", "1", "2"]


# --- get_structured_tasks tests ---


class TestGetStructuredTasks:
    def test_flat_tasks(self):
        # Root-level tasks with no parent field should all appear at the top level
        tasks = [
            {"id": "1", "title": "A", "position": "0"},
            {"id": "2", "title": "B", "position": "1"},
        ]
        result = get_structured_tasks(tasks)
        assert [t.id for t in result] == ["1", "2"]

    def test_sorts_top_level_by_position(self):
        # Output order follows position, not the order the API returned
        tasks = [
            {"id": "b", "title": "B", "position": "00000000000000000002"},
            {"id": "a", "title": "A", "position": "00000000000000000001"},
        ]
        result = get_structured_tasks(tasks)
        assert [t.id for t in result] == ["a", "b"]

    def test_task_with_subtask(self):
        # Child task should be nested under its parent, not appear at the top level
        tasks = [
            {"id": "1", "title": "Parent", "position": "0"},
            {"id": "2", "title": "Child", "position": "0", "parent": "1"},
        ]
        result = get_structured_tasks(tasks)
        assert [t.id for t in result] == ["1"]
        assert [t.id for t in result[0].subtasks] == ["2"]

    def test_child_listed_before_parent(self):
        # A child appearing before its parent in the input must still nest correctly
        tasks = [
            {"id": "2", "title": "Child", "position": "0", "parent": "1"},
            {"id": "1", "title": "Parent", "position": "0"},
        ]
        result = get_structured_tasks(tasks)
        assert [t.id for t in result] == ["1"]
        assert result[0].is_placeholder_parent is False
        assert [t.id for t in result[0].subtasks] == ["2"]

    def test_orphaned_subtask_creates_placeholder(self):
        # A child whose parent is absent must get a placeholder parent node
        tasks = [
            {"id": "2", "title": "Orphan", "position": "0", "parent": "missing"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert result[0].id == "missing"
        assert result[0].is_placeholder_parent is True
        assert [t.id for t in result[0].subtasks] == ["2"]

    def test_orphans_share_one_placeholder(self):
        # Siblings with the same missing parent must reuse a single placeholder
        tasks = [
            {"id": "c1", "position": "0", "parent": "missing"},
            {"id": "c2", "position": "1", "parent": "missing"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert [t.id for t in result[0].subtasks] == ["c1", "c2"]

    def test_empty_input(self):
        # An empty task list should produce an empty result without errors
        assert get_structured_tasks([]) == []

    def test_multiple_children_under_one_parent(self):
        # Multiple siblings should all end up under the same parent node
        tasks = [
            {"id": "p", "title": "Parent", "position": "0"},
            {"id": "c2", "title": "Child 2", "position": "1", "parent": "p"},
            {"id": "c1", "title": "Child 1", "position": "0", "parent": "p"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert [t.id for t in result[0].subtasks] == ["c1", "c2"]

    def test_tasks_without_position_field(self):
        # Tasks missing the position key should be included and sorted last
        tasks = [
            {"id": "1", "title": "No position"},
            {"id": "2", "title": "Has position", "position": "5"},
        ]
        result = get_structured_tasks(tasks)
        assert [t.id for t in result] == ["2", "1"]


# --- sort_structured_tasks tests ---


class TestSortStructuredTasks:
    def test_sorts_by_position(self):
        # Direct children should be reordered numerically by their position value
        root = StructuredTask({"id": "root"}, is_placeholder_parent=False)
        for task_id in ("2", "1", "3"):
            root.add_subtask(
                StructuredTask({"id": task_id}, is_placeholder_parent=False)
            )
        sort_structured_tasks(root, {"1": 1, "2": 2, "3": 3})
        assert [t.id for t in root.subtasks] == ["1", "2", "3"]

    def test_tasks_without_position_go_last(self):
        # Tasks with no entry in positions_by_id should sort after those that have one
        root = StructuredTask({"id": "root"}, is_placeholder_parent=False)
        root.add_subtask(StructuredTask({"id": "a"}, is_placeholder_parent=False))
        root.add_subtask(StructuredTask({"id": "b"}, is_placeholder_parent=False))
        sort_structured_tasks(root, {"b": 0})
        assert [t.id for t in root.subtasks] == ["b", "a"]

    def test_recursive_sort_of_nested_subtasks(self):
        # Sorting must propagate recursively so grandchildren are also ordered
        child = StructuredTask({"id": "child"}, is_placeholder_parent=False)
        child.add_subtask(StructuredTask({"id": "gc_b"}, is_placeholder_parent=False))
        child.add_subtask(StructuredTask({"id": "gc_a"}, is_placeholder_parent=False))
        root = StructuredTask({"id": "root"}, is_placeholder_parent=False)
        root.add_subtask(child)
        sort_structured_tasks(root, {"gc_a": 1, "gc_b": 2})
        assert [t.id for t in child.subtasks] == ["gc_a", "gc_b"]


# --- serialize_tasks tests ---


def _task(**fields):
    fields.setdefault("status", "needsAction")
    fields.setdefault("updated", "2026-01-01")
    return StructuredTask(fields, is_placeholder_parent=False)


class TestSerializeTasks:
    def test_basic_serialization(self):
        # A top-level task renders as a dash bullet with status and updated lines
        result = serialize_tasks([_task(id="1", title="Task A")], 0)
        assert result == (
            "- Task A (ID: 1)\n  Status: needsAction\n  Updated: 2026-01-01\n\n"
        )

    def test_missing_status_and_updated_show_na(self):
        # Absent status/updated fields should render as N/A
        task = StructuredTask({"id": "1", "title": "T"}, is_placeholder_parent=False)
        result = serialize_tasks([task], 0)
        assert "  Status: N/A\n" in result
        assert "  Updated: N/A\n" in result

    def test_indentation_for_subtasks(self):
        # Subtasks are indented two spaces per level and use an asterisk bullet
        parent = _task(id="p1", title="Parent")
        child = _task(id="c1", title="Sub")
        grandchild = _task(id="g1", title="Deep")
        parent.add_subtask(child)
        child.add_subtask(grandchild)
        result = serialize_tasks([parent], 0)
        assert "\n  * Sub (ID: c1)\n    Status: needsAction\n" in result
        assert "\n    * Deep (ID: g1)\n      Status: needsAction\n" in result

    def test_placeholder_note(self):
        # Placeholder parents render as "Unknown parent" and trigger the footer
        placeholder = StructuredTask({"id": "orphan"}, is_placeholder_parent=True)
        placeholder.add_subtask(_task(id="c1", title="Real"))
        result = serialize_tasks([placeholder], 0)
        assert "- Unknown parent (ID: orphan)" in result
        assert "1 tasks with title Unknown parent are included as placeholders" in (
            result
        )

    def test_no_placeholder_note_without_placeholders(self):
        # The footer must not appear when every parent is present
        result = serialize_tasks([_task(id="1", title="T")], 0)
        assert "placeholders" not in result

    def test_truncates_long_notes(self):
        # Notes longer than 100 chars must be truncated with an ellipsis
        result = serialize_tasks([_task(id="1", title="T", notes="x" * 200)], 0)
        assert f"  Notes: {'x' * 100}...\n" in result

    def test_notes_at_exactly_100_chars_no_ellipsis(self):
        # Notes at exactly the limit must not be truncated
        result = serialize_tasks([_task(id="1", title="T", notes="y" * 100)], 0)
        assert f"  Notes: {'y' * 100}\n" in result

    def test_untitled_task_shows_untitled(self):
        # A non-placeholder task with no title should display "Untitled"
        result = serialize_tasks([_task(id="1")], 0)
        assert "- Untitled (ID: 1)" in result

    def test_due_date_appears_when_present(self):
        # Tasks with a due date should have it rendered in the output
        task = _task(id="1", title="T", due="2026-06-01T00:00:00Z")
        result = serialize_tasks([task], 0)
        assert "  Due: 2026-06-01T00:00:00Z\n" in result

    def test_optional_lines_omitted_when_absent(self):
        # Due, notes, and completed lines must be skipped when the fields are unset
        result = serialize_tasks([_task(id="1", title="T")], 0)
        assert "Due:" not in result
        assert "Notes:" not in result
        assert "Completed:" not in result

    def test_completed_appears_when_present(self):
        # Completed timestamp should be rendered when the field is set
        task = _task(
            id="1", title="T", status="completed", completed="2026-05-01T10:00:00Z"
        )
        result = serialize_tasks([task], 0)
        assert "  Completed: 2026-05-01T10:00:00Z\n" in result


# --- Task List impl tests ---


class TestCreateTaskListImpl:
    @pytest.mark.asyncio
    async def test_creates_task_list(self):
        # Happy path: response must include both the title and the new ID
        mock_service = Mock()
        mock_service.tasklists().insert().execute.return_value = {
            "id": "tl123",
            "title": "My List",
            "updated": "2026-01-01",
        }
        result = await _create_task_list_impl(
            mock_service, "user@example.com", "My List"
        )
        assert "- Title: My List" in result
        assert "- ID: tl123" in result

    @pytest.mark.asyncio
    async def test_passes_title_in_body(self):
        # The insert call must be given the correct title in its body argument
        mock_service = Mock()
        mock_service.tasklists().insert().execute.return_value = {
            "id": "tl1",
            "title": "Work",
        }
        await _create_task_list_impl(mock_service, "user@example.com", "Work")
        mock_service.tasklists().insert.assert_called_with(body={"title": "Work"})


class TestUpdateTaskListImpl:
    @pytest.mark.asyncio
    async def test_updates_title(self):
        # Response string should reflect the updated title
        mock_service = Mock()
        mock_service.tasklists().update().execute.return_value = {
            "id": "tl123",
            "title": "Renamed",
            "updated": "2026-01-02",
        }
        result = await _update_task_list_impl(
            mock_service, "user@example.com", "tl123", "Renamed"
        )
        assert "- Title: Renamed" in result

    @pytest.mark.asyncio
    async def test_passes_correct_id_and_title(self):
        # The update call must send both the list ID and the new title
        mock_service = Mock()
        mock_service.tasklists().update().execute.return_value = {
            "id": "tl123",
            "title": "New Name",
        }
        await _update_task_list_impl(
            mock_service, "user@example.com", "tl123", "New Name"
        )
        mock_service.tasklists().update.assert_called_with(
            tasklist="tl123", body={"id": "tl123", "title": "New Name"}
        )


class TestDeleteTaskListImpl:
    @pytest.mark.asyncio
    async def test_deletes(self):
        # Success message must confirm deletion
        mock_service = Mock()
        result = await _delete_task_list_impl(mock_service, "user@example.com", "tl123")
        assert "Task list tl123 has been deleted" in result

    @pytest.mark.asyncio
    async def test_passes_correct_list_id(self):
        # delete must be called with the exact list ID provided
        mock_service = Mock()
        await _delete_task_list_impl(mock_service, "user@example.com", "tl_xyz")
        mock_service.tasklists().delete.assert_called_with(tasklist="tl_xyz")


class TestClearCompletedTasksImpl:
    @pytest.mark.asyncio
    async def test_clears(self):
        # Confirmation message must mention tasks being cleared
        mock_service = Mock()
        result = await _clear_completed_tasks_impl(
            mock_service, "user@example.com", "tl123"
        )
        assert "cleared from task list tl123" in result

    @pytest.mark.asyncio
    async def test_passes_correct_list_id(self):
        # clear must be invoked with the correct tasklist parameter
        mock_service = Mock()
        await _clear_completed_tasks_impl(mock_service, "user@example.com", "tl_abc")
        mock_service.tasks().clear.assert_called_with(tasklist="tl_abc")


# --- Task impl tests ---


class TestCreateTaskImpl:
    @pytest.mark.asyncio
    async def test_creates_basic_task(self):
        # Response must include the task title and its assigned ID
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {
            "id": "t1",
            "title": "Buy milk",
            "status": "needsAction",
        }
        result = await _create_task_impl(
            mock_service, "user@example.com", "tl123", "Buy milk"
        )
        assert "- Title: Buy milk" in result
        assert "- ID: t1" in result
        assert "Due Date" not in result

    @pytest.mark.asyncio
    async def test_creates_with_parent_and_previous(self):
        # parent and previous kwargs must be forwarded to the insert API call
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {
            "id": "t2",
            "title": "Subtask",
        }
        await _create_task_impl(
            mock_service,
            "user@example.com",
            "tl123",
            "Subtask",
            parent="p1",
            previous="prev1",
        )
        call_kwargs = mock_service.tasks().insert.call_args.kwargs
        assert call_kwargs["parent"] == "p1"
        assert call_kwargs["previous"] == "prev1"

    @pytest.mark.asyncio
    async def test_creates_with_due_and_notes(self):
        # due, notes, and web link must appear in the response when returned
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

    @pytest.mark.asyncio
    async def test_body_contains_title_notes_due(self):
        # The insert body dict must carry title, notes, and due when all are supplied
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {
            "id": "t4",
            "title": "Full",
        }
        await _create_task_impl(
            mock_service,
            "user@example.com",
            "tl123",
            "Full",
            notes="note",
            due="2026-06-01T00:00:00Z",
        )
        mock_service.tasks().insert.assert_called_with(
            tasklist="tl123",
            body={"title": "Full", "notes": "note", "due": "2026-06-01T00:00:00Z"},
        )

    @pytest.mark.asyncio
    async def test_no_optional_params_by_default(self):
        # Without optional args the insert call carries only tasklist and title
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {
            "id": "t5",
            "title": "Simple",
        }
        await _create_task_impl(mock_service, "user@example.com", "tl123", "Simple")
        mock_service.tasks().insert.assert_called_with(
            tasklist="tl123", body={"title": "Simple"}
        )


class TestUpdateTaskImpl:
    @pytest.mark.asyncio
    async def test_updates_title_and_status(self):
        # Updated title and status must be sent and appear in the returned string
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
    async def test_fetches_current_task_first(self):
        # The current task must be read from the same list and task IDs
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {"id": "t1"}
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": ""}
        await _update_task_impl(mock_service, "user@example.com", "tl123", "t1")
        mock_service.tasks().get.assert_called_with(tasklist="tl123", task="t1")
        call_kwargs = mock_service.tasks().update.call_args.kwargs
        assert call_kwargs["tasklist"] == "tl123"
        assert call_kwargs["task"] == "t1"

    @pytest.mark.asyncio
    async def test_preserves_existing_fields(self):
        # With no overrides the update body must carry the existing task data
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
    async def test_defaults_when_current_task_is_sparse(self):
        # Missing title/status fall back to "" and "needsAction"; no notes/due added
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {"id": "t1"}
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": ""}
        await _update_task_impl(mock_service, "user@example.com", "tl123", "t1")
        assert mock_service.tasks().update.call_args.kwargs["body"] == {
            "id": "t1",
            "title": "",
            "status": "needsAction",
        }

    @pytest.mark.asyncio
    async def test_update_body_overrides_notes_and_due(self):
        # Explicitly provided notes and due must overwrite the existing values
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


class TestDeleteTaskImpl:
    @pytest.mark.asyncio
    async def test_deletes_task(self):
        # Response must confirm the task was deleted
        mock_service = Mock()
        result = await _delete_task_impl(
            mock_service, "user@example.com", "tl123", "t1"
        )
        assert "Task t1 has been deleted from task list tl123" in result

    @pytest.mark.asyncio
    async def test_passes_correct_ids(self):
        # delete must be called with both the correct task list and task IDs
        mock_service = Mock()
        await _delete_task_impl(mock_service, "user@example.com", "tl_abc", "task_xyz")
        mock_service.tasks().delete.assert_called_with(
            tasklist="tl_abc", task="task_xyz"
        )


class TestMoveTaskImpl:
    @pytest.mark.asyncio
    async def test_moves_task(self):
        # parent/previous must be forwarded and described in Move Details
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
        call_kwargs = mock_service.tasks().move.call_args.kwargs
        assert call_kwargs["parent"] == "new_parent"
        assert call_kwargs["previous"] == "sibling"
        assert "- Parent Task ID: new_parent" in result
        assert "- Position: 12345" in result
        assert (
            "- Move Details: made a subtask of new_parent, positioned after sibling"
            in result
        )

    @pytest.mark.asyncio
    async def test_moves_to_different_list(self):
        # destinationTasklist must be forwarded and appear in Move Details
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {"id": "t1", "title": "T"}
        result = await _move_task_impl(
            mock_service,
            "user@example.com",
            "tl123",
            "t1",
            destination_task_list="tl999",
        )
        assert mock_service.tasks().move.call_args.kwargs["destinationTasklist"] == (
            "tl999"
        )
        assert "- Move Details: moved to task list tl999" in result

    @pytest.mark.asyncio
    async def test_omits_optional_params_by_default(self):
        # Without optional args the move call carries only IDs and no Move Details
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {"id": "t1", "title": "T"}
        result = await _move_task_impl(mock_service, "user@example.com", "tl123", "t1")
        mock_service.tasks().move.assert_called_with(tasklist="tl123", task="t1")
        assert "Move Details" not in result


# --- list_tasks tests ---


def _list_service(*pages):
    """Mock service whose tasks().list().execute returns each page in turn."""
    mock_service = Mock()
    mock_service.tasks.return_value.list.return_value.execute.side_effect = list(pages)
    return mock_service


def _list_calls(mock_service):
    return [c.kwargs for c in mock_service.tasks.return_value.list.call_args_list]


class TestListTasks:
    @pytest.mark.asyncio
    async def test_no_tasks(self):
        # An empty list should produce a friendly message
        mock_service = _list_service({"items": []})
        result = await _unwrap(list_tasks)(
            service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
        )
        assert result == "No tasks found in task list tl1 for u@e.com."

    @pytest.mark.asyncio
    async def test_renders_structured_tasks(self):
        # Tasks are nested under their parents in the response
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

    @pytest.mark.asyncio
    async def test_fetches_more_pages_until_max_results(self):
        # Follow-up pages request only the remaining count using the page token
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
    async def test_stops_when_no_next_page_token(self):
        # A single page without a token must not trigger another request
        mock_service = _list_service({"items": [{"id": "1"}]})
        result = await _unwrap(list_tasks)(
            service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
        )
        assert len(_list_calls(mock_service)) == 1
        assert "Next page token" not in result

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self):
        # An empty follow-up page must end the loop even if a token is returned
        mock_service = _list_service(
            {"items": [{"id": "1"}], "nextPageToken": "tok1"},
            {"items": [], "nextPageToken": "tok2"},
        )
        await _unwrap(list_tasks)(
            service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
        )
        assert len(_list_calls(mock_service)) == 2

    @pytest.mark.asyncio
    async def test_caps_max_results(self):
        # max_results above the cap is clamped when computing the remaining count
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
    async def test_default_params(self):
        # Default call sends the documented defaults and no optional filters
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
    async def test_due_max_is_adjusted(self):
        # due_max is bumped by a day; the other filters pass through unchanged
        mock_service = _list_service({"items": []})
        await _unwrap(list_tasks)(
            service=mock_service,
            user_google_email="u@e.com",
            task_list_id="tl1",
            due_max="2026-05-20T00:00:00Z",
            due_min="2026-05-01T00:00:00Z",
            completed_min="2026-04-01T00:00:00Z",
            completed_max="2026-04-30T00:00:00Z",
            updated_min="2026-03-01T00:00:00Z",
        )
        params = _list_calls(mock_service)[0]
        assert params["dueMax"] == "2026-05-21T00:00:00Z"
        assert params["dueMin"] == "2026-05-01T00:00:00Z"
        assert params["completedMin"] == "2026-04-01T00:00:00Z"
        assert params["completedMax"] == "2026-04-30T00:00:00Z"
        assert params["updatedMin"] == "2026-03-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_http_error_is_wrapped(self):
        # API errors are re-raised with the formatted re-auth message
        mock_service = _list_service(_http_error(500))
        with pytest.raises(Exception, match="API error"):
            await _unwrap(list_tasks)(
                service=mock_service, user_google_email="u@e.com", task_list_id="tl1"
            )


# --- manage_task dispatcher tests ---


def _manage_task_service():
    mock_service = Mock()
    task = {"id": "t1", "title": "Task", "status": "needsAction"}
    mock_service.tasks().insert().execute.return_value = task
    mock_service.tasks().get().execute.return_value = task
    mock_service.tasks().update().execute.return_value = task
    mock_service.tasks().move().execute.return_value = task
    return mock_service


async def _manage_task(mock_service, **kwargs):
    return await _unwrap(manage_task)(
        service=mock_service, user_google_email="u@e.com", task_list_id="tl1", **kwargs
    )


class TestManageTaskDispatcher:
    @pytest.mark.asyncio
    async def test_invalid_action_raises(self):
        # An unrecognised action string must raise without calling the API
        mock_service = Mock()
        with pytest.raises(UserInputError, match="Invalid action 'fly'"):
            await _manage_task(mock_service, action="fly")
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_create_without_title_raises(self):
        # The create action requires a title
        with pytest.raises(UserInputError, match="'title' is required"):
            await _manage_task(Mock(), action="create")

    @pytest.mark.asyncio
    async def test_create_with_status_raises(self):
        # Passing status to create is explicitly forbidden
        with pytest.raises(UserInputError, match="'status' is only supported"):
            await _manage_task(Mock(), action="create", title="T", status="completed")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["update", "delete", "move"])
    async def test_task_id_required(self, action):
        # update, delete, and move all require task_id
        with pytest.raises(UserInputError, match="'task_id' is required"):
            await _manage_task(Mock(), action=action)

    @pytest.mark.asyncio
    async def test_invalid_status_raises(self):
        # A status other than needsAction/completed must raise before the API is hit
        mock_service = Mock()
        with pytest.raises(UserInputError, match="invalid status"):
            await _manage_task(
                mock_service, action="update", task_id="t1", status="done"
            )
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["create", "update"])
    async def test_invalid_due_raises(self, action):
        # A malformed due date must be rejected before the API is hit
        mock_service = Mock()
        with pytest.raises(UserInputError, match="Invalid due date format"):
            await _manage_task(
                mock_service, action=action, title="T", task_id="t1", due="2026-05-20"
            )
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_create_routes_to_impl(self):
        # A valid create forwards every field to tasks().insert
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
    async def test_update_routes_to_impl(self):
        # A valid update forwards the new status to tasks().update
        mock_service = _manage_task_service()
        result = await _manage_task(
            mock_service, action="update", task_id="t1", status="completed"
        )
        assert result.startswith("Task Updated for u@e.com:")
        body = mock_service.tasks().update.call_args.kwargs["body"]
        assert body["status"] == "completed"

    @pytest.mark.asyncio
    async def test_delete_routes_to_impl(self):
        # A valid delete calls tasks().delete with the list and task IDs
        mock_service = _manage_task_service()
        result = await _manage_task(mock_service, action="delete", task_id="t1")
        assert "Task t1 has been deleted" in result
        mock_service.tasks().delete.assert_called_with(tasklist="tl1", task="t1")

    @pytest.mark.asyncio
    async def test_move_routes_to_impl(self):
        # A valid move forwards destination_task_list to tasks().move
        mock_service = _manage_task_service()
        result = await _manage_task(
            mock_service, action="move", task_id="t1", destination_task_list="tl2"
        )
        assert result.startswith("Task Moved for u@e.com:")
        mock_service.tasks().move.assert_called_with(
            tasklist="tl1", task="t1", destinationTasklist="tl2"
        )

    @pytest.mark.asyncio
    async def test_denied_action_raises(self):
        # Under the "manage" permission level delete is denied before the API is hit
        set_permissions({"tasks": "manage"})
        mock_service = Mock()
        with pytest.raises(UserInputError, match="'delete' action is not allowed"):
            await _manage_task(mock_service, action="delete", task_id="t1")
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_allowed_action_under_manage_level(self):
        # The "manage" permission level still allows create
        set_permissions({"tasks": "manage"})
        result = await _manage_task(
            _manage_task_service(), action="create", title="Task"
        )
        assert result.startswith("Task Created for u@e.com:")


# --- manage_task_list dispatcher tests ---


def _manage_task_list_service():
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


async def _manage_task_list(mock_service, **kwargs):
    return await _unwrap(manage_task_list)(
        service=mock_service, user_google_email="u@e.com", **kwargs
    )


class TestManageTaskListDispatcher:
    @pytest.mark.asyncio
    async def test_invalid_action_raises(self):
        # Unrecognised actions must be rejected without calling the API
        mock_service = Mock()
        with pytest.raises(UserInputError, match="Invalid action 'archive'"):
            await _manage_task_list(mock_service, action="archive")
        assert not mock_service.mock_calls

    @pytest.mark.asyncio
    async def test_create_without_title_raises(self):
        # create requires a title
        with pytest.raises(UserInputError, match="'title' is required"):
            await _manage_task_list(Mock(), action="create")

    @pytest.mark.asyncio
    async def test_update_without_task_list_id_raises(self):
        # update requires task_list_id
        with pytest.raises(UserInputError, match="'task_list_id' is required"):
            await _manage_task_list(Mock(), action="update", title="New")

    @pytest.mark.asyncio
    async def test_update_without_title_raises(self):
        # update also requires a new title
        with pytest.raises(UserInputError, match="'title' is required"):
            await _manage_task_list(Mock(), action="update", task_list_id="tl1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["delete", "clear_completed"])
    async def test_task_list_id_required(self, action):
        # delete and clear_completed require task_list_id
        with pytest.raises(UserInputError, match="'task_list_id' is required"):
            await _manage_task_list(Mock(), action=action)

    @pytest.mark.asyncio
    async def test_create_routes_to_impl(self):
        # A valid create calls tasklists().insert and confirms creation
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(mock_service, action="create", title="List")
        assert result.startswith("Task List Created for u@e.com:")
        mock_service.tasklists().insert.assert_called_with(body={"title": "List"})

    @pytest.mark.asyncio
    async def test_update_routes_to_impl(self):
        # A valid update calls tasklists().update and confirms the change
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(
            mock_service, action="update", task_list_id="tl1", title="Renamed"
        )
        assert result.startswith("Task List Updated for u@e.com:")
        mock_service.tasklists().update.assert_called_with(
            tasklist="tl1", body={"id": "tl1", "title": "Renamed"}
        )

    @pytest.mark.asyncio
    async def test_delete_routes_to_impl(self):
        # A valid delete calls tasklists().delete with the list ID
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(
            mock_service, action="delete", task_list_id="tl1"
        )
        assert "Task list tl1 has been deleted" in result
        mock_service.tasklists().delete.assert_called_with(tasklist="tl1")

    @pytest.mark.asyncio
    async def test_clear_completed_routes_to_impl(self):
        # A valid clear_completed calls tasks().clear with the list ID
        mock_service = _manage_task_list_service()
        result = await _manage_task_list(
            mock_service, action="clear_completed", task_list_id="tl1"
        )
        assert "cleared from task list tl1" in result
        mock_service.tasks().clear.assert_called_with(tasklist="tl1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["delete", "clear_completed"])
    async def test_denied_action_raises(self, action):
        # Under the "manage" permission level destructive list actions are denied
        set_permissions({"tasks": "manage"})
        mock_service = Mock()
        with pytest.raises(UserInputError, match="is not allowed"):
            await _manage_task_list(mock_service, action=action, task_list_id="tl1")
        assert not mock_service.mock_calls
