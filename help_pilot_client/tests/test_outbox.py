# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""The outbox is the reason a hub outage never costs someone their ticket.

These tests stand in for the hub rather than talking to a real one, so they run
on any site with no configuration and still pin down the behaviour that matters:
what happens when the hub is down, when it refuses us, and when a delivery is
replayed.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from help_pilot_client import api, hub, notifications
from help_pilot_client.help_pilot_client.doctype.hp_outbox_ticket import hp_outbox_ticket as outbox

USER = "outbox.user@test.local"
OTHER = "outbox.other@test.local"

FAKE_CONFIG = {
	"url": "http://hub.invalid",
	"host": "hub.invalid",
	"site": "client.test",
	"key": "k",
	"secret": "s",
}


def make_user(email, name):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": name,
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	return email


class BaseOutboxTest(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.user = make_user(USER, "Outbox User")
		cls.other = make_user(OTHER, "Other User")

	def setUp(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all("HP Outbox Ticket", pluck="name"):
			frappe.delete_doc("HP Outbox Ticket", name, force=1, ignore_permissions=True)
		for name in frappe.get_all("HP Ticket Watch", pluck="name"):
			frappe.delete_doc("HP Ticket Watch", name, force=1, ignore_permissions=True)
		# FrappeTestCase rolls back per class, not per test, so notifications
		# from the previous test would otherwise still be sitting here.
		frappe.db.delete("Notification Log", {"for_user": ["in", [USER, OTHER]]})

	def tearDown(self):
		frappe.set_user("Administrator")

	def submit(self, as_user=USER, subject="Printer offline", attachment=None):
		frappe.set_user(as_user)
		try:
			with patch.object(hub, "get_config", return_value=FAKE_CONFIG):
				return api.submit_ticket(
					subject=subject, description="<p>Detail.</p>", department="IT", attachment=attachment
				)
		finally:
			frappe.set_user("Administrator")


class TestOutboxQueueing(BaseOutboxTest):
	def test_the_user_is_done_before_the_hub_is_contacted(self):
		result = self.submit()
		row = frappe.get_doc("HP Outbox Ticket", result["outbox"])

		self.assertEqual(row.delivery_status, "Queued")
		self.assertEqual(row.raised_by, USER)
		self.assertIsNone(row.hub_ticket)
		self.assertEqual(row.attempts, 0)

	def test_reference_is_site_qualified(self):
		row = frappe.get_doc("HP Outbox Ticket", self.submit()["outbox"])
		self.assertTrue(row.reference.startswith(f"{frappe.local.site}-"))

	def test_two_submissions_get_different_references(self):
		a = frappe.get_doc("HP Outbox Ticket", self.submit(subject="One")["outbox"])
		b = frappe.get_doc("HP Outbox Ticket", self.submit(subject="Two")["outbox"])
		self.assertNotEqual(a.reference, b.reference)

	def test_a_blank_subject_is_rejected(self):
		frappe.set_user(USER)
		with patch.object(hub, "get_config", return_value=FAKE_CONFIG):
			with self.assertRaises(frappe.ValidationError):
				api.submit_ticket(subject="   ", description="<p>x</p>", department="IT")
		frappe.set_user("Administrator")


class TestOutboxDelivery(BaseOutboxTest):
	def test_a_successful_delivery_records_the_hub_ticket(self):
		name = self.submit()["outbox"]

		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00042"}):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		row = frappe.get_doc("HP Outbox Ticket", name)
		self.assertEqual(row.delivery_status, "Delivered")
		self.assertEqual(row.hub_ticket, "HT-2026-00042")
		self.assertIsNone(row.last_error)

	def test_an_outage_keeps_the_ticket_queued(self):
		name = self.submit()["outbox"]

		with patch.object(hub, "create_ticket", side_effect=hub.HubUnavailable("hub is down")):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		row = frappe.get_doc("HP Outbox Ticket", name)
		self.assertEqual(row.delivery_status, "Queued")
		self.assertEqual(row.attempts, 1)
		self.assertIn("down", row.last_error)

	def test_it_drains_once_the_hub_returns(self):
		name = self.submit()["outbox"]

		with patch.object(hub, "create_ticket", side_effect=hub.HubUnavailable("down")):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		with patch.object(hub, "is_configured", return_value=True), patch.object(
			hub, "create_ticket", return_value={"name": "HT-2026-00043"}
		):
			outbox.flush_outbox()

		row = frappe.get_doc("HP Outbox Ticket", name)
		self.assertEqual(row.delivery_status, "Delivered")
		self.assertEqual(row.hub_ticket, "HT-2026-00043")

	def test_a_refusal_fails_immediately_instead_of_retrying(self):
		name = self.submit()["outbox"]

		with patch.object(hub, "create_ticket", side_effect=hub.HubRejected("bad credentials")):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		row = frappe.get_doc("HP Outbox Ticket", name)
		self.assertEqual(row.delivery_status, "Failed")
		self.assertIn("credentials", row.last_error)

	def test_giving_up_after_too_many_outages(self):
		name = self.submit()["outbox"]
		frappe.db.set_value("HP Outbox Ticket", name, "attempts", outbox.MAX_ATTEMPTS - 1)

		with patch.object(hub, "create_ticket", side_effect=hub.HubUnavailable("down")):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		self.assertEqual(frappe.db.get_value("HP Outbox Ticket", name, "delivery_status"), "Failed")

	def test_a_delivered_row_is_never_sent_twice(self):
		name = self.submit()["outbox"]

		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00044"}):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		with patch.object(hub, "create_ticket") as again:
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		again.assert_not_called()

	def test_retry_clears_a_failure(self):
		name = self.submit()["outbox"]

		with patch.object(hub, "create_ticket", side_effect=hub.HubRejected("bad key")):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		frappe.set_user(USER)
		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00045"}):
			result = api.retry_delivery(name)
		frappe.set_user("Administrator")

		self.assertEqual(result["delivery_status"], "Delivered")

	def test_you_cannot_retry_someone_elses_ticket(self):
		name = self.submit(as_user=USER)["outbox"]

		frappe.set_user(OTHER)
		with self.assertRaises(frappe.PermissionError):
			api.retry_delivery(name)
		frappe.set_user("Administrator")


class TestAttachments(BaseOutboxTest):
	def make_file(self) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "screenshot.txt",
				"content": "pretend this is a screenshot",
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		return doc.file_url

	def test_an_attachment_follows_the_ticket_to_the_hub(self):
		name = self.submit(attachment=self.make_file())["outbox"]

		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00046"}), patch.object(
			hub, "attach_file", return_value={"name": "f1"}
		) as sent:
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		sent.assert_called_once()
		self.assertEqual(sent.call_args.kwargs["ticket"], "HT-2026-00046")
		self.assertEqual(sent.call_args.kwargs["file_name"], "screenshot.txt")
		self.assertTrue(sent.call_args.kwargs["content_base64"])

	def test_a_failed_attachment_does_not_undo_a_delivered_ticket(self):
		name = self.submit(attachment=self.make_file())["outbox"]

		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00047"}), patch.object(
			hub, "attach_file", side_effect=hub.HubUnavailable("upload failed")
		):
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		row = frappe.get_doc("HP Outbox Ticket", name)
		self.assertEqual(row.delivery_status, "Delivered")
		self.assertEqual(row.hub_ticket, "HT-2026-00047")
		self.assertIn("attachment did not", row.last_error)

	def test_no_attachment_means_no_upload_call(self):
		name = self.submit()["outbox"]

		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00048"}), patch.object(
			hub, "attach_file"
		) as sent:
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		sent.assert_not_called()


class TestMyTicketsView(BaseOutboxTest):
	def test_pending_and_delivered_are_shown_together(self):
		queued = self.submit(subject="Still sending")["outbox"]

		frappe.set_user(USER)
		with patch.object(hub, "get_my_tickets", return_value=[{"name": "HT-1", "subject": "Sent"}]):
			data = api.get_my_tickets()
		frappe.set_user("Administrator")

		self.assertEqual([t["name"] for t in data["tickets"]], ["HT-1"])
		self.assertEqual([p["name"] for p in data["pending"]], [queued])
		self.assertIsNone(data["error"])

	def test_a_hub_outage_still_shows_what_we_have(self):
		queued = self.submit(subject="Still sending")["outbox"]

		frappe.set_user(USER)
		with patch.object(hub, "get_my_tickets", side_effect=hub.HubUnavailable("hub is down")):
			data = api.get_my_tickets()
		frappe.set_user("Administrator")

		self.assertEqual(data["tickets"], [])
		self.assertEqual([p["name"] for p in data["pending"]], [queued])
		self.assertIn("down", data["error"])

	def test_one_persons_outbox_is_not_anothers(self):
		self.submit(as_user=USER, subject="Mine")

		frappe.set_user(OTHER)
		with patch.object(hub, "get_my_tickets", return_value=[]):
			data = api.get_my_tickets()
		frappe.set_user("Administrator")

		self.assertEqual(data["pending"], [])


class TestNotificationSync(BaseOutboxTest):
	def deliver_one(self) -> str:
		name = self.submit()["outbox"]
		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00050"}):
			frappe.get_doc("HP Outbox Ticket", name).deliver()
		return "HT-2026-00050"

	def sync(self, tickets):
		with patch.object(hub, "is_configured", return_value=True), patch.object(
			hub, "get_my_tickets", return_value=tickets
		):
			notifications.sync_ticket_updates()

	def unread(self) -> list[str]:
		return frappe.get_all(
			"Notification Log", filters={"for_user": USER}, pluck="email_content"
		)

	def ticket(self, status="Open", reply_count=0, last_reply_by=None):
		return [
			{
				"name": "HT-2026-00050",
				"subject": "Printer offline",
				"department": "IT",
				"status": status,
				"reply_count": reply_count,
				"last_reply_by": last_reply_by,
			}
		]

	def test_first_sight_records_state_without_shouting(self):
		self.deliver_one()
		self.sync(self.ticket())

		self.assertEqual(self.unread(), [])
		self.assertTrue(frappe.db.exists("HP Ticket Watch", f"{USER}::HT-2026-00050"))

	def test_a_status_change_notifies(self):
		self.deliver_one()
		self.sync(self.ticket())
		self.sync(self.ticket(status="Resolved"))

		messages = self.unread()
		self.assertEqual(len(messages), 1)
		self.assertIn("Resolved", messages[0])

	def test_an_agent_reply_notifies(self):
		self.deliver_one()
		self.sync(self.ticket())
		self.sync(self.ticket(reply_count=1, last_reply_by="agent@test.local"))

		messages = self.unread()
		self.assertEqual(len(messages), 1)
		self.assertIn("replied", messages[0])

	def test_your_own_reply_does_not_notify_you(self):
		self.deliver_one()
		self.sync(self.ticket())
		self.sync(self.ticket(reply_count=1, last_reply_by=USER))

		self.assertEqual(self.unread(), [])

	def test_nothing_changing_notifies_nothing(self):
		self.deliver_one()
		self.sync(self.ticket())
		self.sync(self.ticket())
		self.sync(self.ticket())

		self.assertEqual(self.unread(), [])

	def test_a_hub_outage_is_not_an_error(self):
		self.deliver_one()
		with patch.object(hub, "is_configured", return_value=True), patch.object(
			hub, "get_my_tickets", side_effect=hub.HubUnavailable("down")
		):
			notifications.sync_ticket_updates()

		self.assertEqual(self.unread(), [])
