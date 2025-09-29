import asyncio
import unittest
from unittest.mock import MagicMock, patch

from gcalendar.calendar_tools import create_out_of_office_event


class TestCalendarTools(unittest.TestCase):
    @patch('core.server.server.tool', new=lambda *args, **kwargs: (lambda func: func))
    @patch('gcalendar.calendar_tools.require_google_service', new=lambda *args, **kwargs: (lambda func: func))
    @patch('gcalendar.calendar_tools.handle_http_errors', new=lambda *args, **kwargs: (lambda func: func))
    def test_create_out_of_office_event(self):
        # Mock the service object
        service = MagicMock()
        service.events.return_value.insert.return_value.execute.return_value = {
            "id": "test-event-id",
            "htmlLink": "https://calendar.google.com/event?id=test-event-id",
        }

        # Call the function
        result = asyncio.run(
            create_out_of_office_event(
                service,
                "test@example.com",
                start_time="2025-01-01T10:00:00Z",
                end_time="2025-01-01T11:00:00Z",
            )
        )

        # Check the result
        self.assertIn("Successfully created out of office event", result)
        self.assertIn("https://calendar.google.com/event?id=test-event-id", result)


if __name__ == "__main__":
    unittest.main()
