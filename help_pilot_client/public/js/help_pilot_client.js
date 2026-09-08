// Copyright (c) 2026, Somil Vaishya and contributors
// For license information, please see license.txt

frappe.provide("help_pilot_client");

help_pilot_client.available = null;

$(document).on("toolbar_setup", () => help_pilot_client.mount_button());

$(document).ready(() => {
	// toolbar_setup may already have fired by the time this bundle runs.
	if ($(".navbar").length) {
		help_pilot_client.mount_button();
	}
});

help_pilot_client.mount_button = function () {
	if ($(".hp-help-btn").length) {
		return;
	}

	const $nav = $("header.navbar .navbar-nav").last();
	if (!$nav.length) {
		return;
	}

	const $item = $(`
		<li class="nav-item">
			<button class="hp-help-btn" type="button" title="${__("Raise a help ticket")}">
				<svg class="icon icon-sm"><use href="#icon-help"></use></svg>
				<span>${__("Help")}</span>
			</button>
		</li>
	`);

	$item.find("button").on("click", () => help_pilot_client.raise_ticket());
	$nav.prepend($item);

	// Hide it again if this site has no hub configured, rather than offering a
	// button that can only fail.
	frappe.call({
		method: "help_pilot_client.api.is_available",
		callback: ({ message }) => {
			help_pilot_client.available = !!message;
			$item.toggleClass("hidden", !message);
		},
		error: () => $item.addClass("hidden"),
	});
};

help_pilot_client.raise_ticket = function (on_submit) {
	frappe.call({
		method: "help_pilot_client.api.get_departments",
		freeze: true,
		freeze_message: __("Loading..."),
		callback: ({ message }) => {
			const departments = (message || []).map((d) => d.name);

			if (!departments.length) {
				frappe.msgprint({
					title: __("No departments yet"),
					message: __("The help desk has no departments set up. Ask your administrator to add one."),
					indicator: "orange",
				});
				return;
			}

			help_pilot_client.show_dialog(departments, on_submit);
		},
	});
};

help_pilot_client.show_dialog = function (departments, on_submit) {
	const dialog = new frappe.ui.Dialog({
		title: __("Raise a Ticket"),
		fields: [
			{
				fieldname: "department",
				fieldtype: "Select",
				label: __("Who can help?"),
				options: departments,
				default: departments[0],
				reqd: 1,
			},
			{
				fieldname: "subject",
				fieldtype: "Data",
				label: __("What is the problem?"),
				description: __("One line is enough."),
				reqd: 1,
			},
			{
				fieldname: "description",
				fieldtype: "Text Editor",
				label: __("Any detail that would help"),
				reqd: 1,
			},
			{
				fieldname: "attachment",
				fieldtype: "Attach",
				label: __("Screenshot or file"),
				description: __("Optional."),
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			dialog.disable_primary_action();

			frappe.call({
				method: "help_pilot_client.api.submit_ticket",
				args: values,
				callback: () => {
					dialog.hide();
					frappe.show_alert(
						{
							message: __("Ticket sent. You can track it under My Help Tickets."),
							indicator: "green",
						},
						7
					);
					if (on_submit) {
						on_submit();
					}
				},
				error: () => dialog.enable_primary_action(),
			});
		},
	});

	dialog.show();
	setTimeout(() => dialog.get_field("subject").set_focus(), 200);
};
