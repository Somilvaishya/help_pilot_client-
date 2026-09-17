# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""The extra ticket details, the second attachment, and the polling that feeds
the popup. The hub is mocked throughout, so none of this needs a live one."""

from unittest.mock import patch

import frappe

from help_pilot_client import api, hub
from help_pilot_client.help_pilot_client.doctype.hp_outbox_ticket import hp_outbox_ticket as outbox
from help_pilot_client.tests.test_outbox import FAKE_CONFIG, USER, BaseOutboxTest


class TestTicketDetails(BaseOutboxTest):
	def submit_full(self):
		frappe.set_user(USER)
		try:
			with patch.object(hub, "get_config", return_value=FAKE_CONFIG):
				return api.submit_ticket(
					subject="Invoice screen blank",
					description="<p>Detail.</p>",
					department="IT",
					issue_category="IT - Laptop",
					branch="Kanpur Depot",
					contact_no="98765 43210",
				)
		finally:
			frappe.set_user("Administrator")

	def test_the_extra_details_are_kept_on_the_outbox_row(self):
		row = frappe.get_doc("HP Outbox Ticket", self.submit_full()["outbox"])

		self.assertEqual(row.issue_category, "IT - Laptop")
		self.assertEqual(row.branch, "Kanpur Depot")
		self.assertEqual(row.contact_no, "98765 43210")

	def test_they_reach_the_hub_on_delivery(self):
		name = self.submit_full()["outbox"]

		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00090"}) as sent:
			frappe.get_doc("HP Outbox Ticket", name).deliver()

		kwargs = sent.call_args.kwargs
		self.assertEqual(kwargs["issue_category"], "IT - Laptop")
		self.assertEqual(kwargs["branch"], "Kanpur Depot")
		self.assertEqual(kwargs["contact_no"], "98765 43210")

	def test_a_ticket_without_them_still_works(self):
		frappe.set_user(USER)
		with patch.object(hub, "get_config", return_value=FAKE_CONFIG):
			result = api.submit_ticket(
				subject="Bare ticket", description="<p>x</p>", department="IT"
			)
		frappe.set_user("Administrator")

		with patch.object(hub, "create_ticket", return_value={"name": "HT-2026-00091"}) as sent:
			frappe.get_doc("HP Outbox Ticket", result["outbox"]).deliver()

		kwargs = sent.call_args.kwargs
		self.assertIsNone(kwargs["issue_category"])
		self.assertIsNone(kwargs["branch"])
		self.assertIsNone(kwargs["contact_no"])


class TestFormOptions(BaseOutboxTest):
	def test_the_dialog_gets_what_it_needs(self):
		frappe.db.set_value("User", USER, "mobile_no", "99999 11111")

		frappe.set_user(USER)
		with patch.object(hub, "get_config", return_value=FAKE_CONFIG), patch.object(
			hub, "get_departments", return_value=[{"name": "IT"}]
		):
			frappe.cache().delete_value(api.DEPARTMENT_CACHE_KEY)
			options = api.get_form_options()
		frappe.set_user("Administrator")

		self.assertEqual([d["name"] for d in options["departments"]], ["IT"])
		self.assertEqual(options["contact_no"], "99999 11111")
		# Branch is plain text now, so the form needs nothing to draw it.
		self.assertNotIn("has_branch_doctype", options)

	def test_categories_are_cached_per_department(self):
		frappe.cache().delete_value(f"{api.CATEGORY_CACHE_KEY}:IT")

		frappe.set_user(USER)
		with patch.object(
			hub, "get_categories", return_value=[{"name": "IT - Laptop", "category_name": "Laptop"}]
		) as fetched:
			first = api.get_categories("IT")
			second = api.get_categories("IT")
		frappe.set_user("Administrator")

		self.assertEqual(first, second)
		# Second call must come from cache, not another hub round trip.
		fetched.assert_called_once()


class TestSecondAttachment(BaseOutboxTest):
	def make_file(self):
		"""Return (file_url, file_name).

		Frappe renames a file when one of that name already exists, so the name
		it actually stored is the only one worth asserting on.
		"""
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"later-{frappe.generate_hash(length=6)}.txt",
				"content": "sent after the ticket existed",
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		return doc.file_url, doc.file_name

	def test_a_later_file_goes_to_the_hub(self):
		url, file_name = self.make_file()

		frappe.set_user(USER)
		with patch.object(hub, "get_config", return_value=FAKE_CONFIG), patch.object(
			hub, "get_ticket", return_value={"name": "HT-2026-00092"}
		), patch.object(hub, "attach_file", return_value={"name": "f2"}) as sent:
			api.add_attachment("HT-2026-00092", url)
		frappe.set_user("Administrator")

		kwargs = sent.call_args.kwargs
		self.assertEqual(kwargs["ticket"], "HT-2026-00092")
		self.assertEqual(kwargs["file_name"], file_name)
		self.assertTrue(kwargs["content_base64"])

	def test_ownership_is_checked_before_the_upload(self):
		url, _file_name = self.make_file()

		frappe.set_user(USER)
		with patch.object(hub, "get_config", return_value=FAKE_CONFIG), patch.object(
			hub, "get_ticket", side_effect=frappe.PermissionError
		), patch.object(hub, "attach_file") as sent:
			with self.assertRaises(frappe.PermissionError):
				api.add_attachment("HT-SOMEONE-ELSE", url)
		frappe.set_user("Administrator")

		sent.assert_not_called()

	def test_a_missing_file_is_a_clear_error(self):
		frappe.set_user(USER)
		with patch.object(hub, "get_config", return_value=FAKE_CONFIG), patch.object(
			hub, "get_ticket", return_value={"name": "HT-2026-00093"}
		):
			with self.assertRaises(frappe.ValidationError):
				api.add_attachment("HT-2026-00093", "/private/files/does-not-exist.png")
		frappe.set_user("Administrator")


class TestPolling(BaseOutboxTest):
	def setUp(self):
		super().setUp()
		frappe.cache().delete_value(f"help_pilot_client_poll:{USER}")
		frappe.db.delete("Notification Log", {"for_user": USER})

	def poll(self, since=None, detected=None):
		frappe.set_user(USER)
		try:
			with patch.object(hub, "is_configured", return_value=True), patch(
				"help_pilot_client.notifications.sync_for_user", return_value=detected or []
			):
				return api.poll_updates(since=since)
		finally:
			frappe.set_user("Administrator")

	def log_alert(self, body="The IT team replied to HT-1."):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": "Printer offline",
				"email_content": body,
				"for_user": USER,
				"type": "Alert",
				"document_type": "HP Ticket Watch",
				"document_name": f"{USER}::HT-1",
			}
		).insert(ignore_permissions=True)

	def test_the_first_poll_only_hands_back_the_clock(self):
		self.log_alert()
		result = self.poll()

		# Nothing to replay: the browser has no starting point yet.
		self.assertEqual(result["events"], [])
		self.assertTrue(result["now"])

	def test_a_poll_returns_alerts_raised_since_it_last_looked(self):
		start = self.poll()["now"]
		self.log_alert()
		result = self.poll(since=start)

		self.assertEqual(len(result["events"]), 1)
		self.assertEqual(result["events"][0]["sound"], "hp_reply")
		self.assertIn("replied", result["events"][0]["body"])

	def test_a_status_alert_gets_the_status_sound(self):
		start = self.poll()["now"]
		self.log_alert(body="Your ticket HT-1 is now Resolved.")
		result = self.poll(since=start)

		self.assertEqual(result["events"][0]["sound"], "hp_status")
		self.assertEqual(result["events"][0]["kind"], "status")

	def test_whoever_detects_it_every_tab_still_sees_it(self):
		# The scheduled job found it and wrote the log; this tab detected
		# nothing of its own, and must still pop it.
		start = self.poll()["now"]
		self.log_alert()
		result = self.poll(since=start, detected=[])

		self.assertEqual(len(result["events"]), 1)

	def test_a_second_poll_inside_the_window_costs_the_hub_nothing(self):
		first = self.poll()
		second = self.poll(since=first["now"])

		# Several browser tabs must not multiply into several hub calls.
		self.assertTrue(first["polled"])
		self.assertFalse(second["polled"])

	def test_an_unconfigured_site_polls_quietly(self):
		frappe.set_user(USER)
		with patch.object(hub, "is_configured", return_value=False):
			result = api.poll_updates()
		frappe.set_user("Administrator")

		self.assertFalse(result["polled"])
		self.assertEqual(result["events"], [])

	def test_a_hub_outage_does_not_throw_at_the_browser(self):
		frappe.set_user(USER)
		try:
			with patch.object(hub, "is_configured", return_value=True), patch(
				"help_pilot_client.notifications.sync_for_user",
				side_effect=hub.HubUnavailable("hub is down"),
			):
				result = api.poll_updates()
		finally:
			frappe.set_user("Administrator")

		self.assertFalse(result["polled"])
		self.assertEqual(result["events"], [])


class TestPollEvents(BaseOutboxTest):
	"""sync_for_user has to hand back what it decided to tell the user, so the
	browser can pop it immediately instead of waiting for the next page load."""

	def ticket(self, status="Open", reply_count=0, last_reply_by=None):
		return [
			{
				"name": "HT-2026-00099",
				"subject": "Printer offline",
				"department": "IT",
				"status": status,
				"reply_count": reply_count,
				"last_reply_by": last_reply_by,
			}
		]

	def sync(self, tickets):
		from help_pilot_client import notifications

		with patch.object(hub, "get_my_tickets", return_value=tickets):
			return notifications.sync_for_user(USER)

	def test_first_sight_returns_nothing_to_pop(self):
		self.assertEqual(self.sync(self.ticket()), [])

	def test_a_status_change_comes_back_with_its_sound(self):
		self.sync(self.ticket())
		events = self.sync(self.ticket(status="Resolved"))

		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["sound"], "hp_status")
		self.assertIn("Resolved", events[0]["body"])

	def test_an_agent_reply_comes_back_with_its_sound(self):
		self.sync(self.ticket())
		events = self.sync(self.ticket(reply_count=1, last_reply_by="agent@test.local"))

		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["sound"], "hp_reply")

	def test_your_own_reply_pops_nothing(self):
		self.sync(self.ticket())
		self.assertEqual(self.sync(self.ticket(reply_count=1, last_reply_by=USER)), [])
