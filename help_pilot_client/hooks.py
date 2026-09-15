app_name = "help_pilot_client"
app_title = "Help Pilot Client"
app_publisher = "Somil Vaishya"
app_description = "Raise Help Pilot tickets from any ERP site, without leaving it"
app_email = "somil@example.com"
app_license = "mit"

# ----------------------------------------------------------------------
# Includes
# ----------------------------------------------------------------------
app_include_js = "/assets/help_pilot_client/js/help_pilot_client.js"
app_include_css = "/assets/help_pilot_client/css/help_pilot_client.css"

# Frappe ships these mp3 files but registers only some of them, and `chime` is
# commented out in its own hooks -- so play_sound("chime") finds no <audio>
# element and silently does nothing. Register our own names against the files
# that are definitely there, and a little louder: 0.1 is inaudible in an office.
sounds = [
	{"name": "hp_new", "src": "/assets/frappe/sounds/chime.mp3", "volume": 0.5},
	{"name": "hp_reply", "src": "/assets/frappe/sounds/email.mp3", "volume": 0.5},
	{"name": "hp_status", "src": "/assets/frappe/sounds/alert.mp3", "volume": 0.5},
	{"name": "hp_urgent", "src": "/assets/frappe/sounds/error.mp3", "volume": 0.6},
]


# ----------------------------------------------------------------------
# Scheduled tasks
# ----------------------------------------------------------------------
scheduler_events = {
	"cron": {
		# Anything the hub has not accepted yet gets another go every 5 minutes.
		"*/5 * * * *": [
			"help_pilot_client.help_pilot_client.doctype.hp_outbox_ticket.hp_outbox_ticket.flush_outbox",
		],
		# Mirror hub status changes and agent replies into local notifications.
		"*/10 * * * *": [
			"help_pilot_client.notifications.sync_ticket_updates",
		],
	}
}
