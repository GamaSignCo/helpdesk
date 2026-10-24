from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from helpdesk.helpdesk.doctype.hd_ticket.hd_ticket import (
    HDTicket,
    close_tickets_after_n_days,
)
from helpdesk.test_utils import (
    auto_close_feedback_settings,
    auto_close_save_failure,
    freeze_time,
    make_auto_close_feedback_ticket,
)


class TestAutoCloseFeedback(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        frappe.set_user("Administrator")
        self.mail = self.enterContext(patch("frappe.sendmail"))
        self.enterContext(patch.object(frappe.db, "commit"))
        self.status = self.enterContext(auto_close_feedback_settings())
        self.stale = add_to_date(now_datetime(), days=-4)

    def test_auto_close_sends_feedback_once_and_preserves_resolution(self):
        ticket = make_auto_close_feedback_ticket(self.status, self.stale)
        resolved_on = get_datetime(ticket.resolution_date)
        resolution_time = ticket.resolution_time
        self.mail.reset_mock()

        close_tickets_after_n_days()
        ticket.reload()

        self.assertEqual(ticket.status, "Closed")
        self.assertEqual(ticket.status_category, "Resolved")
        self.assertEqual(get_datetime(ticket.resolution_date), resolved_on)
        self.assertEqual(ticket.resolution_time, resolution_time)
        self.mail.assert_called_once()
        self.assertEqual(self.mail.call_args.kwargs["reference_name"], ticket.name)
        self.assertEqual(
            self.mail.call_args.kwargs["email_headers"],
            {"X-Auto-Generated": "hd-email-feedback"},
        )
        close_tickets_after_n_days()
        self.mail.assert_called_once()

    def test_auto_close_runs_sla_lifecycle_hook(self):
        ticket = make_auto_close_feedback_ticket(self.status, self.stale)
        original = HDTicket.apply_sla
        with patch.object(HDTicket, "apply_sla", autospec=True, side_effect=original) as apply_sla:
            close_tickets_after_n_days()
        apply_sla.assert_called_once()
        self.assertEqual(apply_sla.call_args.args[0].name, ticket.name)

    def test_feedback_disabled_still_closes_ticket(self):
        ticket = make_auto_close_feedback_ticket(self.status, self.stale)
        frappe.db.set_single_value("HD Settings", "enable_email_ticket_feedback", 0)
        frappe.clear_document_cache("HD Settings", "HD Settings")
        self.mail.reset_mock()
        close_tickets_after_n_days()
        self.assertEqual(ticket.reload().status, "Closed")
        self.mail.assert_not_called()

    def test_different_feedback_trigger_does_not_send(self):
        ticket = make_auto_close_feedback_ticket(self.status, self.stale)
        frappe.db.set_single_value("HD Settings", "send_email_feedback_on_status", "Resolved")
        frappe.clear_document_cache("HD Settings", "HD Settings")
        self.mail.reset_mock()
        close_tickets_after_n_days()
        self.assertEqual(ticket.reload().status, "Closed")
        self.mail.assert_not_called()

    def test_portal_ticket_does_not_send_feedback_email(self):
        ticket = make_auto_close_feedback_ticket(self.status, self.stale, via_customer_portal=1)
        self.mail.reset_mock()
        close_tickets_after_n_days()
        self.assertEqual(ticket.reload().status, "Closed")
        self.mail.assert_not_called()

    def test_already_rated_ticket_does_not_send_feedback_email(self):
        ticket = make_auto_close_feedback_ticket(self.status, self.stale, feedback_rating=1)
        self.mail.reset_mock()
        close_tickets_after_n_days()
        self.assertEqual(ticket.reload().status, "Closed")
        self.mail.assert_not_called()

    def test_cutoff_is_strict_and_recent_activity_stays_open(self):
        instant = now_datetime().replace(microsecond=0)
        with freeze_time(instant):
            cutoff = add_to_date(instant, days=-3)
            stale = make_auto_close_feedback_ticket(self.status, cutoff - timedelta(seconds=1))
            boundary = make_auto_close_feedback_ticket(self.status, cutoff)
            recent = make_auto_close_feedback_ticket(self.status, cutoff + timedelta(seconds=1))
            self.mail.reset_mock()
            close_tickets_after_n_days()
        self.assertEqual(stale.reload().status, "Closed")
        self.assertEqual(boundary.reload().status, self.status)
        self.assertEqual(recent.reload().status, self.status)
        self.mail.assert_called_once()

    def test_disabled_auto_close_keeps_ticket_open(self):
        ticket = make_auto_close_feedback_ticket(self.status, self.stale)
        frappe.db.set_single_value("HD Settings", "auto_close_tickets", 0)
        self.mail.reset_mock()
        close_tickets_after_n_days()
        self.assertEqual(ticket.reload().status, self.status)
        self.mail.assert_not_called()

    def test_save_failure_is_logged_and_other_ticket_still_closes(self):
        failed = make_auto_close_feedback_ticket(self.status, self.stale)
        healthy = make_auto_close_feedback_ticket(self.status, self.stale)
        self.mail.reset_mock()
        with (
            auto_close_save_failure(failed.name),
            patch("frappe.log_error") as log_error,
        ):
            close_tickets_after_n_days()
        self.assertEqual(failed.reload().status, self.status)
        self.assertEqual(healthy.reload().status, "Closed")
        log_error.assert_called_once()
        self.mail.assert_called_once()
        self.assertEqual(self.mail.call_args.kwargs["reference_name"], healthy.name)
