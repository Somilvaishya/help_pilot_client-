# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""What the desk UI on this site is allowed to ask for.

Every method here acts for `frappe.session.user` and nobody else. The caller
never names a requester -- that is the whole reason a compromised browser
session cannot read somebody else's tickets through this app.
"""

import frappe
from frappe import _

from help_pilot_client import hub

DEPARTMENT_CACHE_KEY = "help_pilot_client_departments"
DEPARTMENT_CACHE_TTL = 600


def _me() -> str:
	user = frappe.session.user
	if user in ("Guest", None):
		frappe.throw(_("Please sign in to raise a ticket."), frappe.PermissionError)
	return user


@frappe.whitelist()
def is_available() -> bool:
	"""Should the Help button appear at all on this site?"""
	return hub.is_configured()


@frappe.whitelist()
def get_departments() -> list[dict]:
	"""Departments offered in the dialog, cached so every click is not a round trip."""
	_me()

	cached = frappe.cache().get_value(DEPARTMENT_CACHE_KEY)
	if cached:
		return cached

	departments = hub.get_departments()
	frappe.cache().set_value(DEPARTMENT_CACHE_KEY, departments, expires_in_sec=DEPARTMENT_CACHE_TTL)
	return departments


@frappe.whitelist()
def submit_ticket(
	subject: str, description: str, department: str | None = None, attachment: str | None = None
) -> dict:
	"""Queue a ticket locally and try to deliver it.

	Returns as soon as the row is written. The user is done at that point --
	delivery is the outbox's problem, not theirs.
	"""
	_me()

	if not subject or not subject.strip():
		frappe.throw(_("Please give the ticket a subject."))

	doc = frappe.get_doc(
		{
			"doctype": "HP Outbox Ticket",
			"subject": subject.strip(),
			"description": description,
			"department": department,
			"attachment": attachment,
		}
	).insert()

	return {"outbox": doc.name, "reference": doc.reference}


@frappe.whitelist()
def get_my_tickets(status: str | None = None) -> dict:
	"""Delivered tickets from the hub, plus anything still sitting in the outbox."""
	me = _me()

	pending = frappe.get_all(
		"HP Outbox Ticket",
		filters={"raised_by": me, "delivery_status": ["!=", "Delivered"]},
		fields=["name", "subject", "department", "delivery_status", "last_error", "creation"],
		order_by="creation desc",
	)

	try:
		tickets = hub.get_my_tickets(me, status)
		error = None
	except hub.HubNotConfigured as e:
		tickets, error = [], str(e)
	except (hub.HubUnavailable, hub.HubRejected) as e:
		# Show what we have rather than an empty page: the pending list is still
		# real, and the user should see their ticket did not vanish.
		tickets, error = [], str(e)

	return {"tickets": tickets, "pending": pending, "error": error}


@frappe.whitelist()
def get_ticket(ticket: str) -> dict:
	return hub.get_ticket(_me(), ticket)


@frappe.whitelist()
def add_reply(ticket: str, comment: str) -> dict:
	if not comment or not comment.strip():
		frappe.throw(_("Please type a reply."))
	return hub.add_reply(_me(), ticket, comment)


@frappe.whitelist()
def set_status(ticket: str, status: str) -> dict:
	return hub.set_status(_me(), ticket, status)


@frappe.whitelist()
def retry_delivery(outbox: str) -> dict:
	"""Let someone push a failed row at the hub again without waiting."""
	me = _me()

	doc = frappe.get_doc("HP Outbox Ticket", outbox)
	if doc.raised_by != me and "System Manager" not in frappe.get_roles(me):
		frappe.throw(_("You can only retry your own tickets."), frappe.PermissionError)

	doc.db_set("delivery_status", "Queued", update_modified=False)
	doc.db_set("attempts", 0, update_modified=False)
	doc.reload()
	doc.deliver()

	return {"delivery_status": doc.delivery_status, "hub_ticket": doc.hub_ticket}
