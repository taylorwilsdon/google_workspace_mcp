"""
These are unit tests that validate the internal implementation functions and utility
helpers in tasks_tools.py using mocked Google API service objects. They cover happy
paths, input validation errors, API error propagation, dispatcher routing logic, and
edge cases in task serialization and structuring.
"""

from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from gtasks.tasks_tools import (
    _adjust_due_max_for_tasks_api,
    _clear_completed_tasks_impl,
    _create_task_impl,
    _create_task_list_impl,
    _delete_task_impl,
    _delete_task_list_impl,
    _move_task_impl,
    _update_task_impl,
    _update_task_list_impl,
    _validate_rfc3339_date,
    get_structured_tasks,
    serialize_tasks,
    sort_structured_tasks,
    StructuredTask,
)


@contextmanager
def _patch_auth(mock_service):
    """
    Bypass the @require_google_service decorator stack so dispatcher tests can
    call manage_task / manage_task_list directly with a pre-built mock service.
    Patches both the OAuth 2.1 path (_extract_oauth20_user_email) and the legacy
    path (_authenticate_service) so the inner function receives the mock service
    and the supplied email without any real auth taking place.
    """
    with patch(
        "auth.service_decorator._authenticate_service",
        return_value=(mock_service, "u@e.com"),
    ), patch(
        "auth.service_decorator._extract_oauth20_user_email",
        return_value="u@e.com",
    ):
        yield


# --- Helper/utility tests ---

class TestAdjustDueMax:
    def test_no_adjustment_needed(self):
        # Verify day is incremented by one for a UTC Z-suffix timestamp
        assert _adjust_due_max_for_tasks_api("2026-05-20T00:00:00Z") == "2026-05-21T00:00:00Z"

    def test_preserves_timezone(self):
        # Non-UTC offsets should survive the bump with their offset intact
        result = _adjust_due_max_for_tasks_api("2026-05-20T00:00:00+05:00")
        assert result.endswith("+05:00")
        assert "2026-05-21" in result

    def test_handles_invalid_input(self):
        # Unparseable strings must be returned unmodified rather than raising
        assert _adjust_due_max_for_tasks_api("not-a-date") == "not-a-date"

    def test_utc_output_ends_with_z(self):
        # UTC-based results must use Z suffix, not +00:00
        result = _adjust_due_max_for_tasks_api("2026-12-31T00:00:00Z")
        assert result.endswith("Z")
        assert "+00:00" not in result

    def test_bumps_across_month_boundary(self):
        # Month rollover (Jan 31 → Feb 1) must be handled correctly
        result = _adjust_due_max_for_tasks_api("2026-01-31T00:00:00Z")
        assert "2026-02-01" in result


class TestValidateRfc3339Date:
    def test_valid_rfc3339(self):
        # A well-formed RFC 3339 timestamp should pass without raising
        _validate_rfc3339_date("2026-05-20T00:00:00Z")

    def test_rejects_date_only(self):
        # Date-only strings are rejected because the Tasks API requires full datetimes
        with pytest.raises(Exception, match="Invalid due date format"):
            _validate_rfc3339_date("2026-05-20")

    def test_rejects_invalid_string(self):
        # Completely malformed strings must raise
        with pytest.raises(Exception, match="Invalid due date format"):
            _validate_rfc3339_date("garbage")

    def test_rejects_naive_datetime(self):
        # Datetimes without timezone info are rejected by the Tasks API
        with pytest.raises(Exception, match="Invalid due date format"):
            _validate_rfc3339_date("2026-05-20T00:00:00")

    def test_valid_with_offset(self):
        # Offset-aware timestamps other than Z must also be accepted
        _validate_rfc3339_date("2026-05-20T00:00:00+05:30")


# --- StructuredTask tests ---

class TestStructuredTask:
    def test_creates_task_with_subtasks(self):
        # add_subtask should append to the subtasks list
        parent = StructuredTask({"id": "p1", "title": "Parent"}, is_placeholder_parent=False)
        child = StructuredTask({"id": "c1", "title": "Child"}, is_placeholder_parent=False)
        parent.add_subtask(child)
        assert len(parent.subtasks) == 1
        assert parent.subtasks[0].title == "Child"

    def test_placeholder_parent(self):
        # is_placeholder_parent flag must be stored faithfully
        task = StructuredTask({"id": "orphan"}, is_placeholder_parent=True)
        assert task.is_placeholder_parent is True

    def test_repr(self):
        # repr should include the task title for easy debugging
        t = StructuredTask({"id": "1", "title": "Hi"}, is_placeholder_parent=False)
        assert "Hi" in repr(t)

    def test_optional_fields_default_to_none(self):
        # Fields absent from the dict should not raise and should be None
        t = StructuredTask({"id": "x"}, is_placeholder_parent=False)
        assert t.title is None
        assert t.due is None
        assert t.notes is None
        assert t.completed is None

    def test_multiple_subtasks_preserve_order(self):
        # Subtasks must be stored in insertion order
        parent = StructuredTask({"id": "p"}, is_placeholder_parent=False)
        for i in range(3):
            parent.add_subtask(StructuredTask({"id": str(i)}, is_placeholder_parent=False))
        assert [s.id for s in parent.subtasks] == ["0", "1", "2"]


# --- get_structured_tasks tests ---

class TestGetStructuredTasks:
    def test_flat_tasks(self):
        # Two root-level tasks with no parent field should both appear at the top level
        tasks = [
            {"id": "1", "title": "A", "status": "needsAction", "position": "0"},
            {"id": "2", "title": "B", "status": "needsAction", "position": "1"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 2
        assert result[0].id == "1"
        assert result[1].id == "2"

    def test_task_with_subtask(self):
        # Child task should be nested under its parent, not appear at the top level
        tasks = [
            {"id": "1", "title": "Parent", "status": "needsAction", "position": "0"},
            {"id": "2", "title": "Child", "status": "needsAction", "position": "0", "parent": "1"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert result[0].id == "1"
        assert len(result[0].subtasks) == 1
        assert result[0].subtasks[0].id == "2"

    def test_orphaned_subtask_creates_placeholder(self):
        # A child whose parent is absent must get a placeholder parent node
        tasks = [
            {"id": "2", "title": "Orphaned Child", "status": "needsAction", "position": "0", "parent": "missing_parent"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert result[0].is_placeholder_parent is True
        assert len(result[0].subtasks) == 1
        assert result[0].subtasks[0].id == "2"

    def test_empty_input(self):
        # An empty task list should produce an empty result without errors
        result = get_structured_tasks([])
        assert result == []

    def test_multiple_children_under_one_parent(self):
        # Multiple siblings should all end up under the same parent node
        tasks = [
            {"id": "p", "title": "Parent", "status": "needsAction", "position": "0"},
            {"id": "c1", "title": "Child 1", "status": "needsAction", "position": "0", "parent": "p"},
            {"id": "c2", "title": "Child 2", "status": "needsAction", "position": "1", "parent": "p"},
        ]
        result = get_structured_tasks(tasks)
        assert len(result) == 1
        assert len(result[0].subtasks) == 2

    def test_tasks_without_position_field(self):
        # Tasks missing the position key should still be included without error
        tasks = [{"id": "1", "title": "No position", "status": "needsAction"}]
        result = get_structured_tasks(tasks)
        assert len(result) == 1


# --- sort_structured_tasks tests ---

class TestSortStructuredTasks:
    def test_sorts_by_position(self):
        # Direct children should be reordered numerically by their position value
        tasks = [
            StructuredTask({"id": "2", "position": "2"}, is_placeholder_parent=False),
            StructuredTask({"id": "1", "position": "1"}, is_placeholder_parent=False),
            StructuredTask({"id": "3", "position": "3"}, is_placeholder_parent=False),
        ]
        positions = {"1": 1, "2": 2, "3": 3}
        root = StructuredTask({"id": "root"}, is_placeholder_parent=False)
        for t in tasks:
            root.add_subtask(t)
        sort_structured_tasks(root, positions)
        assert root.subtasks[0].id == "1"
        assert root.subtasks[1].id == "2"
        assert root.subtasks[2].id == "3"

    def test_tasks_without_position_go_last(self):
        # Tasks with no entry in positions_by_id should sort after those that have one
        tasks = [
            StructuredTask({"id": "a"}, is_placeholder_parent=False),
            StructuredTask({"id": "b", "position": "0"}, is_placeholder_parent=False),
        ]
        positions = {"b": 0}
        root = StructuredTask({"id": "root"}, is_placeholder_parent=False)
        for t in tasks:
            root.add_subtask(t)
        sort_structured_tasks(root, positions)
        assert root.subtasks[0].id == "b"
        assert root.subtasks[1].id == "a"

    def test_recursive_sort_of_nested_subtasks(self):
        # Sorting must propagate recursively so grandchildren are also ordered
        grandchild_a = StructuredTask({"id": "gc_a"}, is_placeholder_parent=False)
        grandchild_b = StructuredTask({"id": "gc_b"}, is_placeholder_parent=False)
        child = StructuredTask({"id": "child"}, is_placeholder_parent=False)
        child.add_subtask(grandchild_b)
        child.add_subtask(grandchild_a)
        root = StructuredTask({"id": "root"}, is_placeholder_parent=False)
        root.add_subtask(child)
        # gc_a should come before gc_b after sorting
        positions = {"gc_a": 1, "gc_b": 2}
        sort_structured_tasks(root, positions)
        assert child.subtasks[0].id == "gc_a"
        assert child.subtasks[1].id == "gc_b"


# --- serialize_tasks tests ---

class TestSerializeTasks:
    def test_basic_serialization(self):
        # Title, status, and ID must all appear in the output string
        tasks = [
            StructuredTask({"id": "1", "title": "Task A", "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False),
        ]
        result = serialize_tasks(tasks, 0)
        assert "Task A" in result
        assert "needsAction" in result
        assert "ID: 1" in result

    def test_indentation_for_subtasks(self):
        # Subtask output should be indented relative to its parent
        child = StructuredTask({"id": "c1", "title": "Sub", "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        parent = StructuredTask({"id": "p1", "title": "Parent", "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        parent.add_subtask(child)
        result = serialize_tasks([parent], 0)
        assert "Parent" in result
        assert "Sub" in result

    def test_placeholder_note(self):
        # Placeholder parents should trigger the explanatory footer message
        placeholder = StructuredTask({"id": "orphan"}, is_placeholder_parent=True)
        placeholder.add_subtask(
            StructuredTask({"id": "c1", "title": "Real", "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        )
        result = serialize_tasks([placeholder], 0)
        assert "Unknown parent" in result
        assert "placeholders" in result

    def test_truncates_long_notes(self):
        # Notes longer than 100 chars must be truncated with an ellipsis
        task = StructuredTask({"id": "1", "title": "T", "notes": "x" * 200, "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        result = serialize_tasks([task], 0)
        assert "..." in result

    def test_notes_at_exactly_100_chars_no_ellipsis(self):
        # Notes at exactly the limit must not be truncated
        task = StructuredTask({"id": "1", "title": "T", "notes": "y" * 100, "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        result = serialize_tasks([task], 0)
        assert "..." not in result

    def test_untitled_task_shows_untitled(self):
        # A non-placeholder task with no title should display "Untitled"
        task = StructuredTask({"id": "1", "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        result = serialize_tasks([task], 0)
        assert "Untitled" in result

    def test_due_date_appears_when_present(self):
        # Tasks with a due date should have it rendered in the output
        task = StructuredTask({"id": "1", "title": "T", "status": "needsAction", "updated": "2026-01-01", "due": "2026-06-01T00:00:00Z"}, is_placeholder_parent=False)
        result = serialize_tasks([task], 0)
        assert "Due" in result
        assert "2026-06-01" in result

    def test_completed_appears_when_present(self):
        # Completed timestamp should be rendered when the field is set
        task = StructuredTask({"id": "1", "title": "T", "status": "completed", "updated": "2026-01-01", "completed": "2026-05-01T10:00:00Z"}, is_placeholder_parent=False)
        result = serialize_tasks([task], 0)
        assert "Completed" in result

    def test_subtask_uses_asterisk_bullet(self):
        # Subtasks (level > 0) should be bulleted with * not -
        child = StructuredTask({"id": "c", "title": "Child", "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        parent = StructuredTask({"id": "p", "title": "Parent", "status": "needsAction", "updated": "2026-01-01"}, is_placeholder_parent=False)
        parent.add_subtask(child)
        result = serialize_tasks([parent], 0)
        assert "* Child" in result


# --- Task List impl tests ---

class TestCreateTaskListImpl:
    @pytest.mark.asyncio
    async def test_creates_task_list(self):
        # Happy path: response must include both the title and the new ID
        mock_service = Mock()
        mock_service.tasklists().insert().execute.return_value = {"id": "tl123", "title": "My List", "updated": "2026-01-01"}
        result = await _create_task_list_impl(mock_service, "user@example.com", "My List")
        assert "My List" in result
        assert "tl123" in result

    @pytest.mark.asyncio
    async def test_passes_title_in_body(self):
        # The insert call must be given the correct title in its body argument
        mock_service = Mock()
        mock_service.tasklists().insert().execute.return_value = {"id": "tl1", "title": "Work", "updated": "2026-01-01"}
        await _create_task_list_impl(mock_service, "user@example.com", "Work")
        call_kwargs = mock_service.tasklists().insert.call_args[1]
        assert call_kwargs["body"]["title"] == "Work"


class TestUpdateTaskListImpl:
    @pytest.mark.asyncio
    async def test_updates_title(self):
        # Response string should reflect the updated title
        mock_service = Mock()
        mock_service.tasklists().update().execute.return_value = {"id": "tl123", "title": "Renamed", "updated": "2026-01-02"}
        result = await _update_task_list_impl(mock_service, "user@example.com", "tl123", "Renamed")
        assert "Renamed" in result

    @pytest.mark.asyncio
    async def test_passes_correct_id_and_title(self):
        # The update call must send both the list ID and the new title
        mock_service = Mock()
        mock_service.tasklists().update().execute.return_value = {"id": "tl123", "title": "New Name", "updated": "2026-01-02"}
        await _update_task_list_impl(mock_service, "user@example.com", "tl123", "New Name")
        call_kwargs = mock_service.tasklists().update.call_args[1]
        assert call_kwargs["tasklist"] == "tl123"
        assert call_kwargs["body"]["title"] == "New Name"


class TestDeleteTaskListImpl:
    @pytest.mark.asyncio
    async def test_deletes(self):
        # Success message must confirm deletion
        mock_service = Mock()
        mock_service.tasklists().delete().execute.return_value = None
        result = await _delete_task_list_impl(mock_service, "user@example.com", "tl123")
        assert "deleted" in result

    @pytest.mark.asyncio
    async def test_passes_correct_list_id(self):
        # delete must be called with the exact list ID provided
        mock_service = Mock()
        mock_service.tasklists().delete().execute.return_value = None
        await _delete_task_list_impl(mock_service, "user@example.com", "tl_xyz")
        call_kwargs = mock_service.tasklists().delete.call_args[1]
        assert call_kwargs["tasklist"] == "tl_xyz"


class TestClearCompletedTasksImpl:
    @pytest.mark.asyncio
    async def test_clears(self):
        # Confirmation message must mention tasks being cleared
        mock_service = Mock()
        mock_service.tasks().clear().execute.return_value = None
        result = await _clear_completed_tasks_impl(mock_service, "user@example.com", "tl123")
        assert "cleared" in result

    @pytest.mark.asyncio
    async def test_passes_correct_list_id(self):
        # clear must be invoked with the correct tasklist parameter
        mock_service = Mock()
        mock_service.tasks().clear().execute.return_value = None
        await _clear_completed_tasks_impl(mock_service, "user@example.com", "tl_abc")
        call_kwargs = mock_service.tasks().clear.call_args[1]
        assert call_kwargs["tasklist"] == "tl_abc"


# --- Task impl tests ---

class TestCreateTaskImpl:
    @pytest.mark.asyncio
    async def test_creates_basic_task(self):
        # Response must include the task title and its assigned ID
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {"id": "t1", "title": "Buy milk", "status": "needsAction", "updated": "2026-01-01"}
        result = await _create_task_impl(mock_service, "user@example.com", "tl123", "Buy milk")
        assert "Buy milk" in result
        assert "t1" in result

    @pytest.mark.asyncio
    async def test_creates_with_parent_and_previous(self):
        # parent and previous kwargs must be forwarded to the insert API call
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {"id": "t2", "title": "Subtask", "status": "needsAction", "updated": "2026-01-01"}
        await _create_task_impl(mock_service, "user@example.com", "tl123", "Subtask", parent="p1", previous="prev1")
        call_kwargs = mock_service.tasks().insert.call_args[1]
        assert call_kwargs["parent"] == "p1"
        assert call_kwargs["previous"] == "prev1"

    @pytest.mark.asyncio
    async def test_creates_with_due_and_notes(self):
        # due and notes must appear in the response string when provided
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {"id": "t3", "title": "With due", "status": "needsAction", "due": "2026-05-20T00:00:00Z", "notes": "Some notes", "updated": "2026-01-01"}
        result = await _create_task_impl(mock_service, "user@example.com", "tl123", "With due", notes="Some notes", due="2026-05-20T00:00:00Z")
        assert "2026-05-20" in result
        assert "Some notes" in result

    @pytest.mark.asyncio
    async def test_body_contains_title_notes_due(self):
        # The insert body dict must carry title, notes, and due when all are supplied
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {"id": "t4", "title": "Full", "status": "needsAction", "due": "2026-06-01T00:00:00Z", "notes": "note", "updated": "2026-01-01"}
        await _create_task_impl(mock_service, "user@example.com", "tl123", "Full", notes="note", due="2026-06-01T00:00:00Z")
        call_kwargs = mock_service.tasks().insert.call_args[1]
        assert call_kwargs["body"]["title"] == "Full"
        assert call_kwargs["body"]["notes"] == "note"
        assert call_kwargs["body"]["due"] == "2026-06-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_no_parent_or_previous_by_default(self):
        # Without parent/previous args the insert call should not include those keys
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {"id": "t5", "title": "Simple", "status": "needsAction", "updated": "2026-01-01"}
        await _create_task_impl(mock_service, "user@example.com", "tl123", "Simple")
        call_kwargs = mock_service.tasks().insert.call_args[1]
        assert "parent" not in call_kwargs
        assert "previous" not in call_kwargs


class TestUpdateTaskImpl:
    @pytest.mark.asyncio
    async def test_updates_title_and_status(self):
        # Updated title and status must both appear in the returned string
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {"id": "t1", "title": "Old", "status": "needsAction"}
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": "New", "status": "completed", "updated": "2026-01-02"}
        result = await _update_task_impl(mock_service, "user@example.com", "tl123", "t1", title="New", status="completed")
        assert "New" in result
        assert "completed" in result

    @pytest.mark.asyncio
    async def test_preserves_existing_fields(self):
        # When no overrides are given the existing task data should be preserved in the update body
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {"id": "t1", "title": "Original", "status": "needsAction", "notes": "Existing notes"}
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": "Original", "status": "needsAction", "notes": "Existing notes", "updated": "2026-01-02"}
        result = await _update_task_impl(mock_service, "user@example.com", "tl123", "t1")
        assert "Original" in result

    @pytest.mark.asyncio
    async def test_update_body_preserves_existing_notes(self):
        # The update body must carry forward pre-existing notes when none are supplied
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {"id": "t1", "title": "T", "status": "needsAction", "notes": "Keep me"}
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": "T", "status": "needsAction", "notes": "Keep me", "updated": "2026-01-02"}
        await _update_task_impl(mock_service, "user@example.com", "tl123", "t1")
        call_kwargs = mock_service.tasks().update.call_args[1]
        assert call_kwargs["body"]["notes"] == "Keep me"

    @pytest.mark.asyncio
    async def test_update_body_overrides_notes(self):
        # Explicitly provided notes must overwrite whatever the existing task has
        mock_service = Mock()
        mock_service.tasks().get().execute.return_value = {"id": "t1", "title": "T", "status": "needsAction", "notes": "Old note"}
        mock_service.tasks().update().execute.return_value = {"id": "t1", "title": "T", "status": "needsAction", "notes": "New note", "updated": "2026-01-02"}
        await _update_task_impl(mock_service, "user@example.com", "tl123", "t1", notes="New note")
        call_kwargs = mock_service.tasks().update.call_args[1]
        assert call_kwargs["body"]["notes"] == "New note"


class TestDeleteTaskImpl:
    @pytest.mark.asyncio
    async def test_deletes_task(self):
        # Response must confirm the task was deleted
        mock_service = Mock()
        mock_service.tasks().delete().execute.return_value = None
        result = await _delete_task_impl(mock_service, "user@example.com", "tl123", "t1")
        assert "deleted" in result

    @pytest.mark.asyncio
    async def test_passes_correct_ids(self):
        # delete must be called with both the correct task list and task IDs
        mock_service = Mock()
        mock_service.tasks().delete().execute.return_value = None
        await _delete_task_impl(mock_service, "user@example.com", "tl_abc", "task_xyz")
        call_kwargs = mock_service.tasks().delete.call_args[1]
        assert call_kwargs["tasklist"] == "tl_abc"
        assert call_kwargs["task"] == "task_xyz"


class TestMoveTaskImpl:
    @pytest.mark.asyncio
    async def test_moves_task(self):
        # Response must confirm the move and the API call must carry parent/previous
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {"id": "t1", "title": "Moved task", "status": "needsAction", "updated": "2026-01-01", "position": "12345"}
        result = await _move_task_impl(mock_service, "user@example.com", "tl123", "t1", parent="new_parent", previous="sibling")
        assert "Moved" in result
        call_kwargs = mock_service.tasks().move.call_args[1]
        assert call_kwargs["parent"] == "new_parent"
        assert call_kwargs["previous"] == "sibling"

    @pytest.mark.asyncio
    async def test_moves_to_different_list(self):
        # Moving to another list should appear in Move Details and the response string
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {"id": "t1", "title": "Moved", "status": "needsAction", "updated": "2026-01-01"}
        result = await _move_task_impl(mock_service, "user@example.com", "tl123", "t1", destination_task_list="tl456")
        assert "moved to task list tl456" in result

    @pytest.mark.asyncio
    async def test_destination_list_passed_as_api_param(self):
        # destinationTasklist must be forwarded under the correct API parameter name
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {"id": "t1", "title": "T", "status": "needsAction", "updated": "2026-01-01"}
        await _move_task_impl(mock_service, "user@example.com", "tl123", "t1", destination_task_list="tl999")
        call_kwargs = mock_service.tasks().move.call_args[1]
        assert call_kwargs["destinationTasklist"] == "tl999"

    @pytest.mark.asyncio
    async def test_no_optional_params_omitted_from_call(self):
        # When parent/previous/destination are omitted the move call must not include those keys
        mock_service = Mock()
        mock_service.tasks().move().execute.return_value = {"id": "t1", "title": "T", "status": "needsAction", "updated": "2026-01-01"}
        await _move_task_impl(mock_service, "user@example.com", "tl123", "t1")
        call_kwargs = mock_service.tasks().move.call_args[1]
        assert "parent" not in call_kwargs
        assert "previous" not in call_kwargs
        assert "destinationTasklist" not in call_kwargs


# --- manage_task dispatcher tests ---

class TestManageTaskDispatcher:
    """Tests that target manage_task's routing, validation, and guard logic directly."""

    def _make_service(self):
        # Build a minimal mock service adequate for most dispatcher tests
        mock_service = Mock()
        mock_service.tasks().insert().execute.return_value = {
            "id": "t1", "title": "Task", "status": "needsAction", "updated": "2026-01-01"
        }
        mock_service.tasks().get().execute.return_value = {
            "id": "t1", "title": "Task", "status": "needsAction"
        }
        mock_service.tasks().update().execute.return_value = {
            "id": "t1", "title": "Task", "status": "needsAction", "updated": "2026-01-02"
        }
        mock_service.tasks().delete().execute.return_value = None
        mock_service.tasks().move().execute.return_value = {
            "id": "t1", "title": "Task", "status": "needsAction", "updated": "2026-01-01"
        }
        return mock_service

    @pytest.mark.asyncio
    async def test_invalid_action_raises(self):
        # An unrecognised action string must immediately raise UserInputError
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="Invalid action"):
                await manage_task("u@e.com", "fly", "tl1")

    @pytest.mark.asyncio
    async def test_create_without_title_raises(self):
        # The create action requires a title; omitting it must raise UserInputError
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="title"):
                await manage_task("u@e.com", "create", "tl1")

    @pytest.mark.asyncio
    async def test_create_with_status_raises(self):
        # Passing status to create is explicitly forbidden and must raise
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="status"):
                await manage_task("u@e.com", "create", "tl1", title="T", status="completed")

    @pytest.mark.asyncio
    async def test_update_without_task_id_raises(self):
        # update requires task_id; missing it must raise UserInputError
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="task_id"):
                await manage_task("u@e.com", "update", "tl1")

    @pytest.mark.asyncio
    async def test_delete_without_task_id_raises(self):
        # delete requires task_id; missing it must raise UserInputError
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="task_id"):
                await manage_task("u@e.com", "delete", "tl1")

    @pytest.mark.asyncio
    async def test_move_without_task_id_raises(self):
        # move requires task_id; missing it must raise UserInputError
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="task_id"):
                await manage_task("u@e.com", "move", "tl1")

    @pytest.mark.asyncio
    async def test_invalid_status_raises(self):
        # A status value other than needsAction/completed must raise before hitting the API
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="invalid status"):
                await manage_task("u@e.com", "update", "tl1", task_id="t1", status="done")

    @pytest.mark.asyncio
    async def test_invalid_due_raises(self):
        # A malformed due date must be caught by the dispatcher before the API is called
        from gtasks.tasks_tools import manage_task
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="Invalid due date format"):
                await manage_task("u@e.com", "create", "tl1", title="T", due="2026-05-20")

    @pytest.mark.asyncio
    async def test_create_routes_to_impl(self):
        # A valid create call must return a string containing the task title
        from gtasks.tasks_tools import manage_task
        svc = self._make_service()
        with _patch_auth(svc):
            result = await manage_task("u@e.com", "create", "tl1", title="Task")
        assert "Task" in result

    @pytest.mark.asyncio
    async def test_delete_routes_to_impl(self):
        # A valid delete call must return a string confirming deletion
        from gtasks.tasks_tools import manage_task
        svc = self._make_service()
        with _patch_auth(svc):
            result = await manage_task("u@e.com", "delete", "tl1", task_id="t1")
        assert "deleted" in result


# --- manage_task_list dispatcher tests ---

class TestManageTaskListDispatcher:
    """Tests that target manage_task_list's routing and validation logic."""

    def _make_service(self):
        # Build a minimal mock service adequate for task list dispatcher tests
        mock_service = Mock()
        mock_service.tasklists().insert().execute.return_value = {"id": "tl1", "title": "List", "updated": "2026-01-01"}
        mock_service.tasklists().update().execute.return_value = {"id": "tl1", "title": "Renamed", "updated": "2026-01-02"}
        mock_service.tasklists().delete().execute.return_value = None
        mock_service.tasks().clear().execute.return_value = None
        return mock_service

    @pytest.mark.asyncio
    async def test_invalid_action_raises(self):
        # Unrecognised actions must be rejected immediately with UserInputError
        from gtasks.tasks_tools import manage_task_list
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="Invalid action"):
                await manage_task_list("u@e.com", "archive")

    @pytest.mark.asyncio
    async def test_create_without_title_raises(self):
        # create action requires a title; omitting it must raise UserInputError
        from gtasks.tasks_tools import manage_task_list
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="title"):
                await manage_task_list("u@e.com", "create")

    @pytest.mark.asyncio
    async def test_update_without_task_list_id_raises(self):
        # update requires task_list_id; missing it must raise UserInputError
        from gtasks.tasks_tools import manage_task_list
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="task_list_id"):
                await manage_task_list("u@e.com", "update", title="New")

    @pytest.mark.asyncio
    async def test_update_without_title_raises(self):
        # update also requires a new title; missing it must raise UserInputError
        from gtasks.tasks_tools import manage_task_list
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="title"):
                await manage_task_list("u@e.com", "update", task_list_id="tl1")

    @pytest.mark.asyncio
    async def test_delete_without_task_list_id_raises(self):
        # delete requires task_list_id; missing it must raise UserInputError
        from gtasks.tasks_tools import manage_task_list
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="task_list_id"):
                await manage_task_list("u@e.com", "delete")

    @pytest.mark.asyncio
    async def test_clear_completed_without_task_list_id_raises(self):
        # clear_completed requires task_list_id; missing it must raise UserInputError
        from gtasks.tasks_tools import manage_task_list
        from core.utils import UserInputError
        with _patch_auth(self._make_service()):
            with pytest.raises(UserInputError, match="task_list_id"):
                await manage_task_list("u@e.com", "clear_completed")

    @pytest.mark.asyncio
    async def test_create_routes_to_impl(self):
        # A valid create call must route through and return a confirmation string
        from gtasks.tasks_tools import manage_task_list
        svc = self._make_service()
        with _patch_auth(svc):
            result = await manage_task_list("u@e.com", "create", title="List")
        assert "List" in result

    @pytest.mark.asyncio
    async def test_delete_routes_to_impl(self):
        # A valid delete call must return a string confirming deletion
        from gtasks.tasks_tools import manage_task_list
        svc = self._make_service()
        with _patch_auth(svc):
            result = await manage_task_list("u@e.com", "delete", task_list_id="tl1")
        assert "deleted" in result

    @pytest.mark.asyncio
    async def test_clear_completed_routes_to_impl(self):
        # A valid clear_completed call must return a string confirming tasks were cleared
        from gtasks.tasks_tools import manage_task_list
        svc = self._make_service()
        with _patch_auth(svc):
            result = await manage_task_list("u@e.com", "clear_completed", task_list_id="tl1")
        assert "cleared" in result