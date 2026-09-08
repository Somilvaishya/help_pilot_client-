# Help Pilot Client

Install this on every ERP site that is **not** the Help Pilot hub.

It adds a **Help** button to the desk navbar. Anyone can raise a ticket in three
fields without leaving the site they are working in; the ticket is queued
locally and relayed to the hub, where the IT / ERP / HR teams work it.

The hub is a separate site running [`help_pilot`](https://github.com/Somilvaishya/help_pilot).
This app stores no tickets of its own — only an outbox row per submission, which
is deleted once the hub confirms delivery.

## Why an outbox

The user is told "submitted" the moment the row is written, before the hub is
contacted. A hub restart, a migration, or a network blip therefore never costs
someone the ticket they just typed — the delivery is retried in the background.

## Configuration

Per site, in `site_config.json`:

```json
"help_pilot_hub_url":    "http://127.0.0.1:8000",
"help_pilot_hub_host":   "erp15.mevabite.com",
"help_pilot_site_name":  "regency.mevabite.com",
"help_pilot_api_key":    "...",
"help_pilot_api_secret": "..."
```

`help_pilot_hub_host` is only needed when several sites share one bench: the
request goes to loopback and this header tells the webserver which site it is
for. Drop it when the hub is on its own server.

On the hub, create a user whose **only** role is `HP Bridge`, generate its API
key and secret, and add an `HP Source Site` row naming it.

## Install

```bash
bench get-app help_pilot_client https://github.com/Somilvaishya/help_pilot_client.git
bench --site <site> install-app help_pilot_client
```

## License

MIT
