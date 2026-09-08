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
"help_pilot_hub_host":   "erp.example.com",
"help_pilot_site_name":  "erp.example.com",
"help_pilot_api_key":    "...",
"help_pilot_api_secret": "..."
```

`help_pilot_hub_host` is only needed when several sites share one bench: the
request goes to loopback and this header tells the webserver which site it is
for. Drop it when the hub is on its own server.

On the hub, create a user whose **only** role is `HP Bridge`, generate its API
key and secret, and add an `HP Source Site` row naming it.

## What it adds to the site

| | |
| --- | --- |
| **Help** button in the navbar | Three fields plus an optional screenshot. Hidden if the site has no hub configured. |
| **My Help Tickets** page | Tickets pulled live from the hub, the conversation thread, replies, and close / reopen. Anything still queued is listed above them. |
| `HP Outbox Ticket` | One row per submission. Deleted by nobody — keep it as the audit trail of what this site sent. |
| `HP Ticket Watch` | What this site last saw of each ticket, so it can notice changes. |

## Notifications

The hub raises a Notification Log when an agent replies — but on the *hub*, for
a Website User who never signs in there. Nobody would ever see it.

So this site polls the hub every 10 minutes and mirrors changes into local
notifications, where they will actually be read. It stays quiet on purpose:

* A **status change** always notifies.
* A **new reply** only notifies when somebody else wrote it — otherwise every
  message a requester sends bounces straight back at them.
* The **first** time a ticket is seen, nothing is announced. There is no point
  replaying history the person already lived through.

## Attachments

The file is uploaded to this site first, then pushed to the hub ticket *after*
the ticket is accepted. If the upload fails the ticket still stands and the
reason is recorded on the outbox row — a file that would not upload must never
send the whole delivery back through the retry loop and risk a second ticket.

## Scheduled jobs

| Job | Every |
| --- | --- |
| `flush_outbox` | 5 minutes — retries anything the hub has not accepted |
| `sync_ticket_updates` | 10 minutes — mirrors hub changes into local notifications |

Both need the scheduler enabled and a background worker running.

## Install

```bash
bench get-app help_pilot_client https://github.com/Somilvaishya/help_pilot_client.git
bench --site <site> install-app help_pilot_client
```

## License

MIT
