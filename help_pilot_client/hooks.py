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
