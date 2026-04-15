"""Tests for src/emailer.py - TDD approach."""

import unittest
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from unittest.mock import MagicMock, patch, call
import email


class TestSendNewsletter(unittest.TestCase):
    """Tests for send_newsletter function."""

    def setUp(self):
        """Set up common test fixtures."""
        self.html_content = "<html><body><h1>Test Newsletter</h1></body></html>"
        self.plain_content = "Test Newsletter - plain text version"
        self.run_time_am = datetime(2026, 4, 14, 9, 30, 0)   # 9:30 AM
        self.run_time_pm = datetime(2026, 4, 14, 15, 0, 0)   # 3:00 PM
        self.test_sender = "sender@gmail.com"
        self.test_password = "test_app_password"
        self.test_recipients = ["recipient1@example.com", "recipient2@example.com"]

    def _make_smtp_mock(self):
        """Create a MagicMock that works as an SMTP context manager."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        smtp_class = MagicMock(return_value=smtp_instance)
        return smtp_class, smtp_instance

    @patch("src.emailer.RECIPIENTS", ["recipient1@example.com", "recipient2@example.com"])
    @patch.dict("os.environ", {"GMAIL_SENDER": "sender@gmail.com", "GMAIL_APP_PASSWORD": "test_app_password"})
    @patch("smtplib.SMTP")
    def test_smtp_instantiated_with_correct_host_and_port(self, mock_smtp_class):
        """Test (a): smtplib.SMTP is instantiated with host='smtp.gmail.com' and port=587."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        mock_smtp_class.return_value = smtp_instance

        from src.emailer import send_newsletter
        send_newsletter(self.html_content, self.plain_content, self.run_time_am)

        mock_smtp_class.assert_called_once_with("smtp.gmail.com", 587)

    @patch("src.emailer.RECIPIENTS", ["recipient1@example.com", "recipient2@example.com"])
    @patch.dict("os.environ", {"GMAIL_SENDER": "sender@gmail.com", "GMAIL_APP_PASSWORD": "test_app_password"})
    @patch("smtplib.SMTP")
    def test_starttls_is_called(self, mock_smtp_class):
        """Test (b): starttls() is called on the SMTP instance."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        mock_smtp_class.return_value = smtp_instance

        from src.emailer import send_newsletter
        send_newsletter(self.html_content, self.plain_content, self.run_time_am)

        smtp_instance.starttls.assert_called_once()

    @patch("src.emailer.RECIPIENTS", ["recipient1@example.com", "recipient2@example.com"])
    @patch.dict("os.environ", {"GMAIL_SENDER": "sender@gmail.com", "GMAIL_APP_PASSWORD": "test_app_password"})
    @patch("smtplib.SMTP")
    def test_login_called_with_credentials(self, mock_smtp_class):
        """Test (c): login() is called with GMAIL_SENDER and GMAIL_APP_PASSWORD values."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        mock_smtp_class.return_value = smtp_instance

        from src.emailer import send_newsletter
        send_newsletter(self.html_content, self.plain_content, self.run_time_am)

        smtp_instance.login.assert_called_once_with("sender@gmail.com", "test_app_password")

    @patch("src.emailer.RECIPIENTS", ["recipient1@example.com", "recipient2@example.com"])
    @patch.dict("os.environ", {"GMAIL_SENDER": "sender@gmail.com", "GMAIL_APP_PASSWORD": "test_app_password"})
    @patch("smtplib.SMTP")
    def test_message_is_mime_multipart_alternative_with_both_parts(self, mock_smtp_class):
        """Test (d): the sent message is MIMEMultipart('alternative') containing both plain-text and HTML parts."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        mock_smtp_class.return_value = smtp_instance

        from src.emailer import send_newsletter
        send_newsletter(self.html_content, self.plain_content, self.run_time_am)

        # Check sendmail was called
        smtp_instance.sendmail.assert_called_once()
        call_args = smtp_instance.sendmail.call_args
        raw_message = call_args[0][2]  # third positional arg is msg.as_string()

        # Parse the raw message
        msg = email.message_from_string(raw_message)
        self.assertEqual(msg.get_content_type(), "multipart/alternative")

        payloads = msg.get_payload()
        content_types = [part.get_content_type() for part in payloads]
        self.assertIn("text/plain", content_types)
        self.assertIn("text/html", content_types)

    @patch("src.emailer.RECIPIENTS", ["recipient1@example.com", "recipient2@example.com"])
    @patch.dict("os.environ", {"GMAIL_SENDER": "sender@gmail.com", "GMAIL_APP_PASSWORD": "test_app_password"})
    @patch("smtplib.SMTP")
    def test_subject_contains_date_and_am(self, mock_smtp_class):
        """Test (e): subject line contains run_time date and 'AM' for morning hours."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        mock_smtp_class.return_value = smtp_instance

        from src.emailer import send_newsletter
        send_newsletter(self.html_content, self.plain_content, self.run_time_am)

        smtp_instance.sendmail.assert_called_once()
        call_args = smtp_instance.sendmail.call_args
        raw_message = call_args[0][2]
        msg = email.message_from_string(raw_message)

        subject = msg["Subject"]
        expected_date = self.run_time_am.strftime("%B %d, %Y")
        self.assertIn(expected_date, subject)
        self.assertIn("AM", subject)
        self.assertNotIn("PM", subject)

    @patch("src.emailer.RECIPIENTS", ["recipient1@example.com", "recipient2@example.com"])
    @patch.dict("os.environ", {"GMAIL_SENDER": "sender@gmail.com", "GMAIL_APP_PASSWORD": "test_app_password"})
    @patch("smtplib.SMTP")
    def test_subject_contains_date_and_pm(self, mock_smtp_class):
        """Test (e continued): subject line contains run_time date and 'PM' for afternoon hours."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        mock_smtp_class.return_value = smtp_instance

        from src.emailer import send_newsletter
        send_newsletter(self.html_content, self.plain_content, self.run_time_pm)

        smtp_instance.sendmail.assert_called_once()
        call_args = smtp_instance.sendmail.call_args
        raw_message = call_args[0][2]
        msg = email.message_from_string(raw_message)

        subject = msg["Subject"]
        expected_date = self.run_time_pm.strftime("%B %d, %Y")
        self.assertIn(expected_date, subject)
        self.assertIn("PM", subject)
        self.assertNotIn("AM", subject)

    @patch("src.emailer.RECIPIENTS", ["recipient1@example.com", "recipient2@example.com"])
    @patch.dict("os.environ", {"GMAIL_SENDER": "sender@gmail.com", "GMAIL_APP_PASSWORD": "test_app_password"})
    @patch("smtplib.SMTP")
    def test_all_recipients_in_to_header(self, mock_smtp_class):
        """Test (f): all addresses in RECIPIENTS appear in the message To header."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        mock_smtp_class.return_value = smtp_instance

        from src.emailer import send_newsletter
        send_newsletter(self.html_content, self.plain_content, self.run_time_am)

        smtp_instance.sendmail.assert_called_once()
        call_args = smtp_instance.sendmail.call_args
        raw_message = call_args[0][2]
        msg = email.message_from_string(raw_message)

        to_header = msg["To"]
        for recipient in ["recipient1@example.com", "recipient2@example.com"]:
            self.assertIn(recipient, to_header)


if __name__ == "__main__":
    unittest.main()
