# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

import base64
import os
import uuid

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from help_pilot_client import hub

MAX_ATTEMPTS = 12


class HPOutboxTicket(Document):
	def before_insert(self):
		self.raised_by = frappe.session.user
		self.delivery_status = "Queued"
		self.attempts = 0
		if not self.reference:
			# Site-qualified so two sites can never collide on the hub.
			self.reference = f"{frappe.local.site}-{uuid.uuid4().hex[:16]}"

	def after_insert(self):
		frappe.enqueue(
			"help_pilot_client.help_pilot_client.doctype.hp_outbox_ticket.hp_outbox_ticket.deliver",
			queue="short",
			name=self.name,
			enqueue_after_commit=True,
		)

	def deliver(self):
		"""Hand this row to the hub. Safe to call repeatedly."""
		if self.delivery_status == "Delivered":
			return

		self.db_set("attempts", (self.attempts or 0) + 1, update_modified=False)
		self.db_set("last_attempt_on", now_datetime(), update_modified=False)

		try:
			result = hub.create_ticket(
				requester_email=self.raised_by,
				requester_full_name=frappe.db.get_value("User", self.raised_by, "full_name"),
				subject=self.subject,
				description=self.description,
				department=self.department or None,
				source_reference=self.reference,
			)
		except hub.HubUnavailable as e:
			# Keep it queued; the scheduler will come back to it.
			self.db_set("last_error", str(e), update_modified=False)
			if (self.attempts or 0) >= MAX_ATTEMPTS:
				self.db_set("delivery_status", "Failed", update_modified=False)
			return
		except (hub.HubRejected, hub.HubNotConfigured) as e:
			# Retrying cannot help. Stop, and leave the reason where a human
			# will see it.
			self.db_set("delivery_status", "Failed", update_modified=False)
			self.db_set("last_error", str(e), update_modified=False)
			return

		self.db_set("hub_ticket", result.get("name"), update_modified=False)
		self.db_set("delivery_status", "Delivered", update_modified=False)
		self.db_set("last_error", None, update_modified=False)

		self.push_attachment()

	def push_attachment(self):
		"""Send the attached file on to the hub ticket.

		Deliberately after the ticket is marked Delivered: the ticket itself is
		what matters, and a file that fails to upload must not send the whole
		row back through the retry loop and risk a second ticket.
		"""
		if not self.attachment or not self.hub_ticket:
			return

		try:
			content = _read_local_file(self.attachment)
		except Exception:
			frappe.log_error(
				title="Help Pilot: could not read attachment", message=frappe.get_traceback()
			)
			return

		if content is None:
			return

		try:
			hub.attach_file(
				requester_email=self.raised_by,
				ticket=self.hub_ticket,
				file_name=os.path.basename(self.attachment.split("?")[0]),
				content_base64=base64.b64encode(content).decode(),
			)
		except (hub.HubUnavailable, hub.HubRejected, hub.HubNotConfigured) as e:
			# The ticket is already on the hub; note the miss and move on rather
			# than pretending the whole delivery failed.
			self.db_set("last_error", f"Ticket sent, attachment did not: {e}", update_modified=False)


def _read_local_file(file_url: str) -> bytes | None:
	"""Read a File this site holds, private or public.

	`get_content()` hands back a str for anything it considers text and bytes
	otherwise, so normalise before the caller tries to base64 it.
	"""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return None

	content = frappe.get_doc("File", name).get_content()
	if isinstance(content, str):
		return content.encode("utf-8")

	return content


def deliver(name: str):
	"""Background entry point."""
	doc = frappe.get_doc("HP Outbox Ticket", name)
	doc.deliver()
	frappe.db.commit()


def flush_outbox():
	"""Scheduled drain of anything the hub has not accepted yet."""
	pending = frappe.get_all(
		"HP Outbox Ticket",
		filters={"delivery_status": "Queued", "attempts": ["<", MAX_ATTEMPTS]},
		pluck="name",
		order_by="creation asc",
		limit_page_length=200,
	)

	if not pending:
		return

	if not hub.is_configured():
		return

	for name in pending:
		try:
			frappe.get_doc("HP Outbox Ticket", name).deliver()
		except Exception:
			frappe.log_error(
				title="Help Pilot outbox delivery failed", message=frappe.get_traceback()
			)
		frappe.db.commit()
