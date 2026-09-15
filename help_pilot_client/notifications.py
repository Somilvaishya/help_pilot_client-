# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Tell people, on their own site, when their ticket moves.

The hub raises a Notification Log when an agent replies -- but it raises it on
the hub, for a Website User who never signs in there. Nobody sees it. So this
site polls the hub for the tickets its own users raised and mirrors the changes
into local notifications, where they will actually be read.

Two rules keep it quiet enough to stay useful:

* A status change is always worth telling someone about.
* A new reply is only worth it when somebody else wrote it -- otherwise every
  message a requester sends bounces straight back at them.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from help_pilot_client import hub

PAGE_LINK = "/app/my-help-tickets"


def sync_ticket_updates():
	"""Scheduled: mirror hub changes into local notifications."""
	if not hub.is_configured():
		return

	users = frappe.get_all(
		"HP Outbox Ticket",
		filters={"delivery_status": "Delivered", "hub_ticket": ["is", "set"]},
		pluck="raised_by",
		distinct=True,
	)

	for user in users:
		if not frappe.db.get_value("User", user, "enabled"):
			continue
		try:
			sync_for_user(user)
		except (hub.HubUnavailable, hub.HubNotConfigured):
			# The hub is down. Nothing to mirror; try again next tick.
			return
		except Exception:
			frappe.log_error(
				title="Help Pilot: notification sync failed", message=frappe.get_traceback()
			)

	frappe.db.commit()


def sync_for_user(user: str) -> list[dict]:
	"""Reconcile and return whatever is worth popping at the user right now."""
	fresh = []
	for ticket in hub.get_my_tickets(user):
		fresh.extend(_reconcile(user, ticket))
	return fresh


def _reconcile(user: str, ticket: dict) -> list[dict]:
	name = f"{user}::{ticket['name']}"
	watch = frappe.db.get_value(
		"HP Ticket Watch", name, ["name", "last_status", "last_reply_count"], as_dict=True
	)

	status = ticket.get("status")
	reply_count = ticket.get("reply_count") or 0
	last_reply_by = ticket.get("last_reply_by")

	if not watch:
		# First sight of this ticket. Record where it is; do not announce
		# history the person has already lived through.
		frappe.get_doc(
			{
				"doctype": "HP Ticket Watch",
				"user": user,
				"hub_ticket": ticket["name"],
				"last_status": status,
				"last_reply_count": reply_count,
				"last_seen_on": now_datetime(),
			}
		).insert(ignore_permissions=True)
		return []

	messages = []
	sound = "hp_new"

	if status != watch.last_status:
		messages.append(_("Your ticket {0} is now {1}.").format(ticket["name"], _(status)))
		sound = "hp_status"

	if reply_count > (watch.last_reply_count or 0) and last_reply_by != user:
		messages.append(_("The {0} team replied to {1}.").format(ticket.get("department"), ticket["name"]))
		sound = "hp_reply"

	if messages:
		_notify(user, ticket, " ".join(messages))

	frappe.db.set_value(
		"HP Ticket Watch",
		watch.name,
		{"last_status": status, "last_reply_count": reply_count, "last_seen_on": now_datetime()},
		update_modified=False,
	)

	if not messages:
		return []

	return [
		{
			"kind": "status" if sound == "hp_status" else "reply",
			"title": ticket.get("subject") or ticket["name"],
			"body": " ".join(messages),
			"ticket": ticket["name"],
			"sound": sound,
		}
	]


def _notify(user: str, ticket: dict, message: str):
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"subject": ticket.get("subject") or ticket["name"],
			"email_content": message,
			"for_user": user,
			"type": "Alert",
			"link": PAGE_LINK,
			# Tagged so a browser poll can pick out Help Pilot's own alerts.
			"document_type": "HP Ticket Watch",
			"document_name": f"{user}::{ticket['name']}",
		}
	).insert(ignore_permissions=True)
