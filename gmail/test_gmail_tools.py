import asyncio
import unittest
from unittest.mock import MagicMock, patch

from gmail.gmail_tools import get_vacation_settings, set_vacation_settings


class TestGmailTools(unittest.TestCase):
    @patch('core.server.server.tool', new=lambda *args, **kwargs: (lambda func: func))
    @patch('gmail.gmail_tools.require_google_service', new=lambda *args, **kwargs: (lambda func: func))
    @patch('gmail.gmail_tools.handle_http_errors', new=lambda *args, **kwargs: (lambda func: func))
    def test_vacation_settings(self):
        # Mock the service object
        service = MagicMock()
        service.users.return_value.settings.return_value.getVacation.return_value.execute.return_value = {
            "enableAutoReply": False,
        }
        service.users.return_value.settings.return_value.updateVacation.return_value.execute.return_value = {}

        # 1. Get original settings
        original_settings = asyncio.run(
            get_vacation_settings(service, "test@example.com")
        )
        self.assertIn("Enabled: False", original_settings)

        # 2. Set new settings
        asyncio.run(
            set_vacation_settings(
                service,
                "test@example.com",
                enable=True,
                subject="On vacation",
                body="I am on vacation.",
            )
        )

        # 3. Get new settings to confirm
        service.users.return_value.settings.return_value.getVacation.return_value.execute.return_value = {
            "enableAutoReply": True,
            "responseSubject": "On vacation",
            "responseBodyHtml": "I am on vacation.",
        }
        new_settings = asyncio.run(get_vacation_settings(service, "test@example.com"))
        self.assertIn("Enabled: True", new_settings)
        self.assertIn("Subject: On vacation", new_settings)
        self.assertIn("Body: I am on vacation.", new_settings)

        # 4. Restore original settings
        asyncio.run(
            set_vacation_settings(
                service, "test@example.com", enable=False
            )
        )
        service.users.return_value.settings.return_value.getVacation.return_value.execute.return_value = {
            "enableAutoReply": False,
        }
        restored_settings = asyncio.run(
            get_vacation_settings(service, "test@example.com")
        )
        self.assertIn("Enabled: False", restored_settings)


if __name__ == "__main__":
    unittest.main()
