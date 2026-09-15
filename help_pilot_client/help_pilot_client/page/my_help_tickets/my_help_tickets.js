// Copyright (c) 2026, Somil Vaishya and contributors
// For license information, please see license.txt

frappe.pages["my-help-tickets"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("My Help Tickets"),
		single_column: true,
	});

	const view = new HelpTicketList(page);
	wrapper.help_ticket_list = view;
};

frappe.pages["my-help-tickets"].on_page_show = function (wrapper) {
	if (wrapper.help_ticket_list) {
		wrapper.help_ticket_list.refresh();
	}
};

const STATUS_COLOR = {
	Open: "orange",
	"In Progress": "blue",
	Resolved: "green",
	Closed: "gray",
	Reopened: "red",
};

class HelpTicketList {
	constructor(page) {
		this.page = page;
		this.status = null;

		this.page.set_primary_action(__("Raise a Ticket"), () =>
			help_pilot_client.raise_ticket(() => this.refresh())
		);

		this.status_field = this.page.add_select(
			__("Status"),
			["", "Open", "In Progress", "Resolved", "Closed", "Reopened"].map((v) => ({
				label: v || __("All statuses"),
				value: v,
			}))
		);
		this.status_field.on("change", () => {
			this.status = this.status_field.val() || null;
			this.refresh();
		});

		this.$body = $('<div class="hp-list"></div>').appendTo(this.page.main);
		this.refresh();
	}

	refresh() {
		frappe.call({
			method: "help_pilot_client.api.get_my_tickets",
			args: { status: this.status },
			callback: ({ message }) => this.render(message || {}),
		});
	}

	render(data) {
		this.$body.empty();

		if (data.error) {
			$(`<div class="hp-banner">${frappe.utils.escape_html(data.error)}</div>`).appendTo(this.$body);
		}

		const pending = data.pending || [];
		const tickets = data.tickets || [];

		if (pending.length) {
			$(`<div class="hp-section-label">${__("Still sending")}</div>`).appendTo(this.$body);
			pending.forEach((row) => this.pending_card(row).appendTo(this.$body));
		}

		if (!tickets.length && !pending.length) {
			$(`
				<div class="hp-empty">
					<p>${__("You have not raised any tickets yet.")}</p>
				</div>
			`).appendTo(this.$body);
			return;
		}

		if (tickets.length) {
			if (pending.length) {
				$(`<div class="hp-section-label">${__("Sent")}</div>`).appendTo(this.$body);
			}
			tickets.forEach((row) => this.ticket_card(row).appendTo(this.$body));
		}
	}

	ticket_card(row) {
		const color = STATUS_COLOR[row.status] || "gray";
		const $card = $(`
			<div class="hp-ticket-card">
				<div class="hp-ticket-main">
					<div class="hp-ticket-subject">${frappe.utils.escape_html(row.subject)}</div>
					<div class="hp-ticket-meta">
						${frappe.utils.escape_html(row.name)} &middot;
						${frappe.utils.escape_html(row.department)} &middot;
						${__("updated")} ${frappe.datetime.comment_when(row.modified)}
					</div>
				</div>
				<span class="indicator-pill ${color}">${__(row.status)}</span>
			</div>
		`);

		$card.on("click", () => this.open_ticket(row.name));
		return $card;
	}

	pending_card(row) {
		const failed = row.delivery_status === "Failed";
		const $card = $(`
			<div class="hp-ticket-card hp-pending">
				<div class="hp-ticket-main">
					<div class="hp-ticket-subject">${frappe.utils.escape_html(row.subject)}</div>
					<div class="hp-ticket-meta">
						${
							failed
								? __("Could not send: {0}", [frappe.utils.escape_html(row.last_error || "")])
								: __("Waiting to reach the help desk")
						}
					</div>
				</div>
				<span class="indicator-pill ${failed ? "red" : "orange"}">
					${failed ? __("Failed") : __("Sending")}
				</span>
			</div>
		`);

		if (failed) {
			$card.on("click", () => {
				frappe.call({
					method: "help_pilot_client.api.retry_delivery",
					args: { outbox: row.name },
					freeze: true,
					freeze_message: __("Sending..."),
					callback: () => this.refresh(),
				});
			});
		}

		return $card;
	}

	open_ticket(name) {
		frappe.call({
			method: "help_pilot_client.api.get_ticket",
			args: { ticket: name },
			freeze: true,
			callback: ({ message }) => this.show_ticket(message),
		});
	}

	show_ticket(ticket) {
		const color = STATUS_COLOR[ticket.status] || "gray";

		const dialog = new frappe.ui.Dialog({
			title: ticket.subject,
			size: "large",
			fields: [
				{ fieldname: "head", fieldtype: "HTML" },
				{ fieldname: "thread", fieldtype: "HTML" },
				{ fieldtype: "Section Break" },
				{
					fieldname: "reply",
					fieldtype: "Small Text",
					label: __("Add a reply"),
				},
				{
					fieldname: "more_file",
					fieldtype: "Attach",
					label: __("Add another screenshot or file"),
					description: __("Sent to the team as soon as you pick it."),
					onchange: () => {
						const url = dialog.get_value("more_file");
						if (!url) {
							return;
						}
						frappe.call({
							method: "help_pilot_client.api.add_attachment",
							args: { ticket: ticket.name, file_url: url },
							freeze: true,
							freeze_message: __("Sending the file..."),
							callback: () => {
								dialog.set_value("more_file", "");
								help_pilot_client.play("submit");
								frappe.show_alert({ message: __("File sent"), indicator: "green" });
								this.open_ticket(ticket.name);
							},
						});
					},
				},
			],
			primary_action_label: __("Reply"),
			primary_action: ({ reply }) => {
				if (!reply || !reply.trim()) {
					return;
				}
				frappe.call({
					method: "help_pilot_client.api.add_reply",
					args: { ticket: ticket.name, comment: `<p>${frappe.utils.escape_html(reply)}</p>` },
					callback: () => {
						dialog.hide();
						this.refresh();
						frappe.show_alert({ message: __("Reply sent"), indicator: "green" });
					},
				});
			},
		});

		const bits = [frappe.utils.escape_html(ticket.name), frappe.utils.escape_html(ticket.department)];
		if (ticket.issue_category) {
			bits.push(frappe.utils.escape_html(ticket.issue_category));
		}
		if (ticket.branch) {
			bits.push(frappe.utils.escape_html(ticket.branch));
		}

		const files = ticket.attachments || [];
		const files_html = files.length
			? `<div class="hp-files">${files
					.map(
						(f) =>
							`<span class="hp-file">${frappe.utils.escape_html(f.file_name)}</span>`
					)
					.join("")}</div>`
			: "";

		dialog.get_field("head").$wrapper.html(`
			<div class="hp-ticket-meta" style="margin-bottom: 12px">
				${bits.join(" &middot; ")} &middot;
				<span class="indicator-pill ${color}">${__(ticket.status)}</span>
			</div>
			<div>${frappe.dom.remove_script_and_style(ticket.description)}</div>
			${files_html}
		`);

		const comments = ticket.comments || [];
		dialog.get_field("thread").$wrapper.html(
			comments.length
				? `<div class="hp-thread">${comments
						.map(
							(c) => `
								<div class="hp-comment hp-${c.is_mine ? "requester" : "agent"}">
									<div class="hp-comment-head">
										<span class="hp-author">${frappe.utils.escape_html(c.full_name)}</span>
										<span class="hp-time">${frappe.datetime.comment_when(c.creation)}</span>
									</div>
									<div class="hp-comment-body">${frappe.dom.remove_script_and_style(c.comment)}</div>
								</div>`
						)
						.join("")}</div>`
				: `<div class="text-muted">${__("No replies yet.")}</div>`
		);

		if (ticket.can_close) {
			dialog.set_secondary_action_label(__("This is fixed — close it"));
			dialog.set_secondary_action(() => this.change_status(dialog, ticket.name, "Closed"));
		} else if (ticket.can_reopen) {
			dialog.set_secondary_action_label(__("Reopen"));
			dialog.set_secondary_action(() => this.change_status(dialog, ticket.name, "Reopened"));
		}

		dialog.show();
	}

	change_status(dialog, ticket, status) {
		frappe.call({
			method: "help_pilot_client.api.set_status",
			args: { ticket, status },
			callback: () => {
				dialog.hide();
				this.refresh();
				frappe.show_alert({ message: __("Updated"), indicator: "green" });
			},
		});
	}
}
