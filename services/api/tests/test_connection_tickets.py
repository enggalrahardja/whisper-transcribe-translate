import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.security import Principal, _ticket_principal, issue_websocket_ticket


class TicketSettings:
    security_connection_ticket_secret = "test-secret-with-enough-entropy"
    security_connection_ticket_ttl_seconds = 30


class ConnectionTicketTests(unittest.TestCase):
    def test_ticket_is_short_lived_and_scoped_to_one_session(self):
        with patch("app.security.get_settings", return_value=TicketSettings()):
            ticket, expires_at = issue_websocket_ticket(Principal("smr-service"), "session-a")
            self.assertLessEqual(expires_at, int(time.time()) + 30)
            self.assertEqual(_ticket_principal(ticket, "session-a").user_id, "smr-service")
            with self.assertRaises(HTTPException):
                _ticket_principal(ticket, "session-b")

    def test_tampered_ticket_is_rejected(self):
        with patch("app.security.get_settings", return_value=TicketSettings()):
            ticket, _ = issue_websocket_ticket(Principal("smr-service"), "session-a")
            with self.assertRaises(HTTPException):
                _ticket_principal(ticket + "x", "session-a")
