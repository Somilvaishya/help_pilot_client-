# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""The only place this app talks to the Help Pilot hub.

Everything else calls `hub.call(...)`. Two classes of failure matter and are
kept apart deliberately:

* **Retryable** -- the hub is restarting, the network blipped, we got a 5xx.
  The outbox keeps the row and tries again.
* **Permanent** -- bad credentials, an unknown source site, a validation error.
  Retrying will never help, so the row is failed immediately and a human is
  told why.

Getting that distinction wrong is how a queue either spins forever on a typo in
`site_config.json`, or silently drops a ticket because the hub was rebooting.
"""

import json

import requests

import frappe
from frappe import _

DEFAULT_TIMEOUT = 15


class HubNotConfigured(frappe.ValidationError):
	pass


class HubUnavailable(frappe.ValidationError):
	"""Worth retrying."""


class HubRejected(frappe.ValidationError):
	"""Not worth retrying."""


def get_config(throw: bool = True) -> dict | None:
	conf = frappe.conf
	config = {
		"url": (conf.get("help_pilot_hub_url") or "").rstrip("/"),
		"host": conf.get("help_pilot_hub_host"),
		"site": conf.get("help_pilot_site_name") or frappe.local.site,
		"key": conf.get("help_pilot_api_key"),
		"secret": conf.get("help_pilot_api_secret"),
	}

	if not (config["url"] and config["key"] and config["secret"]):
		if throw:
			raise HubNotConfigured(
				_("Help Pilot is not configured on this site. Add help_pilot_hub_url, help_pilot_api_key and help_pilot_api_secret to site_config.json.")
			)
		return None

	return config


def is_configured() -> bool:
	return get_config(throw=False) is not None


def call(method: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
	"""POST to a whitelisted method on the hub and return its `message`."""
	config = get_config()

	headers = {
		"Authorization": f"token {config['key']}:{config['secret']}",
		"Content-Type": "application/json",
		"Accept": "application/json",
	}

	# Several sites share one bench and one webserver, so the request goes to
	# loopback and this header is what routes it to the hub site.
	if config["host"]:
		headers["Host"] = config["host"]

	url = f"{config['url']}/api/method/{method}"

	try:
		response = requests.post(url, data=json.dumps(params or {}), headers=headers, timeout=timeout)
	except requests.exceptions.Timeout as e:
		raise HubUnavailable(_("The help desk did not respond in time.")) from e
	except requests.exceptions.ConnectionError as e:
		raise HubUnavailable(_("Could not reach the help desk.")) from e

	if response.status_code == 200:
		try:
			return response.json().get("message")
		except ValueError as e:
			raise HubUnavailable(_("The help desk sent a response we could not read.")) from e

	message = _extract_error(response)

	# 429 is the one 4xx worth retrying: we are being throttled, not refused.
	if response.status_code >= 500 or response.status_code == 429:
		raise HubUnavailable(message)

	raise HubRejected(message)


def _extract_error(response) -> str:
	"""Pull the human-readable half out of a Frappe error response."""
	try:
		payload = response.json()
	except ValueError:
		return f"HTTP {response.status_code}"

	if not isinstance(payload, dict):
		return f"HTTP {response.status_code}"

	# Frappe puts a throw()'s text in _server_messages: a JSON string holding a
	# list of JSON strings. Two layers, and either can be malformed.
	raw = payload.get("_server_messages")
	if raw:
		try:
			messages = json.loads(raw)
			first = messages[0] if messages else None
			if isinstance(first, str):
				first = json.loads(first)
			text = first.get("message") if isinstance(first, dict) else first
			if text:
				return frappe.utils.strip_html(str(text)).strip()
		except (ValueError, TypeError, AttributeError, IndexError):
			pass

	for key in ("exception", "message", "exc_type"):
		if payload.get(key):
			return str(payload[key])

	return f"HTTP {response.status_code}"


# ----------------------------------------------------------------------
# Thin wrappers, so call sites never build method paths by hand
# ----------------------------------------------------------------------
def _site() -> str:
	return get_config()["site"]


def create_ticket(**kwargs):
	return call("help_pilot.bridge.create_ticket", {"source_site": _site(), **kwargs})


def get_categories(department: str | None = None):
	return (
		call(
			"help_pilot.bridge.get_categories",
			{"source_site": _site(), "department": department},
		)
		or []
	)


def get_attachments(requester_email: str, ticket: str):
	return (
		call(
			"help_pilot.bridge.get_attachments",
			{"source_site": _site(), "requester_email": requester_email, "ticket": ticket},
		)
		or []
	)


def get_departments():
	return call("help_pilot.bridge.get_departments", {"source_site": _site()}) or []


def get_my_tickets(requester_email: str, status: str | None = None):
	return (
		call(
			"help_pilot.bridge.get_my_tickets",
			{"source_site": _site(), "requester_email": requester_email, "status": status},
		)
		or []
	)


def get_ticket(requester_email: str, ticket: str):
	return call(
		"help_pilot.bridge.get_ticket",
		{"source_site": _site(), "requester_email": requester_email, "ticket": ticket},
	)


def add_reply(requester_email: str, ticket: str, comment: str):
	return call(
		"help_pilot.bridge.add_reply",
		{
			"source_site": _site(),
			"requester_email": requester_email,
			"ticket": ticket,
			"comment": comment,
		},
	)


def attach_file(requester_email: str, ticket: str, file_name: str, content_base64: str):
	return call(
		"help_pilot.bridge.attach_file",
		{
			"source_site": _site(),
			"requester_email": requester_email,
			"ticket": ticket,
			"file_name": file_name,
			"content_base64": content_base64,
		},
		timeout=60,
	)


def set_status(requester_email: str, ticket: str, status: str):
	return call(
		"help_pilot.bridge.set_status",
		{
			"source_site": _site(),
			"requester_email": requester_email,
			"ticket": ticket,
			"status": status,
		},
	)
