# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""What the desk UI on this site is allowed to ask for.

Every method here acts for `frappe.session.user` and nobody else. The caller
never names a requester -- that is the whole reason a compromised browser
session cannot read somebody else's tickets through this app.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from help_pilot_client import hub, notifications

DEPARTMENT_CACHE_KEY = "help_pilot_client_departments"
DEPARTMENT_CACHE_TTL = 600
CATEGORY_CACHE_KEY = "help_pilot_client_categories"

# The browser polls every 60s, but a person may have several tabs open. One hub
# round trip per user per window keeps that from multiplying.
POLL_THROTTLE_SEC = 45


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

	# `expires=True` matters: without it a miss is written into frappe.local
	# cache as None, and `set_value` with a TTL never updates that, so every
	# later read in the same request misses again and re-fetches.
	cached = frappe.cache().get_value(DEPARTMENT_CACHE_KEY, expires=True)
	if cached:
		return cached

	departments = hub.get_departments()
	frappe.cache().set_value(DEPARTMENT_CACHE_KEY, departments, expires_in_sec=DEPARTMENT_CACHE_TTL)
	return departments


@frappe.whitelist()
def get_categories(department: str | None = None) -> list[dict]:
	"""Issue categories for one department, cached so typing is not a round trip."""
	_me()

	key = f"{CATEGORY_CACHE_KEY}:{department or 'all'}"
	cached = frappe.cache().get_value(key, expires=True)
	if cached is not None:
		return cached

	categories = hub.get_categories(department)
	frappe.cache().set_value(key, categories, expires_in_sec=DEPARTMENT_CACHE_TTL)
	return categories


@frappe.whitelist()
def get_form_options() -> dict:
	"""Everything the Raise a Ticket dialog needs to draw itself."""
	me = _me()

	contact = frappe.db.get_value("User", me, ["mobile_no", "phone"], as_dict=True) or {}

	return {
		"departments": get_departments(),
		"contact_no": contact.get("mobile_no") or contact.get("phone") or "",
	}


@frappe.whitelist()
def submit_ticket(
	subject: str,
	description: str,
	department: str | None = None,
	attachment: str | None = None,
	issue_category: str | None = None,
	branch: str | None = None,
	contact_no: str | None = None,
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
			"issue_category": issue_category,
			"branch": branch,
			"contact_no": contact_no,
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
def add_attachment(ticket: str, file_url: str) -> dict:
	"""Send one more file to a ticket that is already on the hub."""
	me = _me()

	from help_pilot_client.help_pilot_client.doctype.hp_outbox_ticket.hp_outbox_ticket import (
		_read_local_file,
	)

	# Reading the ticket also proves this person owns it.
	hub.get_ticket(me, ticket)

	content = _read_local_file(file_url)
	if content is None:
		frappe.throw(_("That file is no longer on this site."))

	import base64
	import os

	return hub.attach_file(
		requester_email=me,
		ticket=ticket,
		file_name=os.path.basename(file_url.split("?")[0]),
		content_base64=base64.b64encode(content).decode(),
	)


@frappe.whitelist()
def poll_updates(since: str | None = None) -> dict:
	"""What to pop at this user right now.

	Detecting a change and showing it are kept apart on purpose. Whoever notices
	first -- the scheduled job, or any one of this person's open tabs -- writes a
	Notification Log. Every tab then pops whatever appeared since *it* last
	looked. Tie them together and whichever ran first eats the event, and the
	user sees only the bell count move, which reads as "it arrives on refresh".

	The hub round trip is throttled per user, so ten open tabs still cost the hub
	one call. The first call carries no `since` and returns nothing to pop: it
	only hands back the clock, so old unread alerts are not replayed.
	"""
	me = _me()
	now = str(now_datetime())

	if not hub.is_configured():
		return {"events": [], "polled": False, "now": now}

	polled = False
	guard = f"help_pilot_client_poll:{me}"

	if not frappe.cache().get_value(guard, expires=True):
		frappe.cache().set_value(guard, 1, expires_in_sec=POLL_THROTTLE_SEC)
		try:
			notifications.sync_for_user(me)
			polled = True
		except (hub.HubUnavailable, hub.HubRejected, hub.HubNotConfigured):
			# The hub is down. Nothing new to find; still deliver anything the
			# last successful check left behind.
			pass
		frappe.db.commit()

	return {"events": _alerts_since(me, since), "polled": polled, "now": now}


def _alerts_since(user: str, since: str | None) -> list[dict]:
	if not since:
		return []

	rows = frappe.get_all(
		"Notification Log",
		filters={
			"for_user": user,
			"read": 0,
			"document_type": "HP Ticket Watch",
			"creation": [">", since],
		},
		fields=["subject", "email_content", "document_name"],
		order_by="creation asc",
		limit_page_length=10,
	)

	events = []
	for row in rows:
		body = row.email_content or ""
		# The Notification Log has nowhere to keep which sound it wanted, and the
		# two things worth saying are distinguishable from the text itself.
		is_reply = "replied" in body
		events.append(
			{
				"kind": "reply" if is_reply else "status",
				"title": row.subject,
				"body": body,
				"ticket": (row.document_name or "").split("::")[-1],
				"sound": "hp_reply" if is_reply else "hp_status",
			}
		)

	return events


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
