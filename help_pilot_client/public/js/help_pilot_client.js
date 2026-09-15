// Copyright (c) 2026, Somil Vaishya and contributors
// For license information, please see license.txt

frappe.provide("help_pilot_client");

help_pilot_client.available = null;
help_pilot_client.options = null;
help_pilot_client.poll_timer = null;

// The hub is a different site, so there is no websocket to it. The browser asks
// once a minute instead; the server throttles the hub round trip per user, so
// several open tabs still cost the hub one call.
help_pilot_client.POLL_MS = 60000;
help_pilot_client.SOUND_KEY = "help_pilot_sound_on";

// ----------------------------------------------------------------------
// Sound and popup
// ----------------------------------------------------------------------
help_pilot_client.sound_enabled = function () {
	try {
		return localStorage.getItem(help_pilot_client.SOUND_KEY) !== "0";
	} catch (e) {
		return true;
	}
};

help_pilot_client.set_sound = function (on) {
	try {
		localStorage.setItem(help_pilot_client.SOUND_KEY, on ? "1" : "0");
	} catch (e) {
		// Private window or blocked site data; the choice just will not stick.
	}
};

help_pilot_client.play = function (name) {
	if (!help_pilot_client.sound_enabled()) {
		return;
	}
	try {
		frappe.utils.play_sound(name || "chime");
	} catch (e) {
		// Browsers refuse audio before the first interaction. Silence is fine.
	}
};

help_pilot_client.alert = function (data) {
	if (!data || !data.title) {
		return;
	}

	help_pilot_client.play(data.sound);

	const $msg = $(`
		<div class="hp-toast">
			<div class="hp-toast-title">${frappe.utils.escape_html(data.title)}</div>
			${data.body ? `<div class="hp-toast-body">${frappe.utils.escape_html(data.body)}</div>` : ""}
			<div class="hp-toast-open">${__("Open My Help Tickets")} &rarr;</div>
		</div>
	`);

	frappe.show_alert(
		{ message: $msg.prop("outerHTML"), indicator: data.kind === "status" ? "green" : "blue" },
		12
	);

	$(".hp-toast")
		.last()
		.css("cursor", "pointer")
		.on("click", () => frappe.set_route("my-help-tickets"));

	help_pilot_client.desktop(data);
};

help_pilot_client.desktop = function (data) {
	if (typeof Notification === "undefined" || !document.hidden) {
		return;
	}
	if (Notification.permission !== "granted") {
		return;
	}
	try {
		const n = new Notification(data.title, {
			body: data.body || "",
			tag: data.ticket || "help-pilot",
		});
		n.onclick = function () {
			window.focus();
			frappe.set_route("my-help-tickets");
			n.close();
		};
	} catch (e) {
		// Not supported on some mobile browsers.
	}
};

help_pilot_client.ask_desktop_permission = function () {
	if (typeof Notification === "undefined" || Notification.permission !== "default") {
		return;
	}
	// Asking cold gets denied forever. Ask on the first click instead.
	$(document).one("click", () => {
		try {
			Notification.requestPermission();
		} catch (e) {
			// Blocked by policy.
		}
	});
};

// ----------------------------------------------------------------------
// Polling
// ----------------------------------------------------------------------
help_pilot_client.start_polling = function () {
	if (help_pilot_client.poll_timer) {
		return;
	}
	help_pilot_client.poll_timer = setInterval(help_pilot_client.poll, help_pilot_client.POLL_MS);
	// Coming back to the tab is exactly when someone wants to know.
	$(document).on("visibilitychange", () => {
		if (!document.hidden) {
			help_pilot_client.poll();
		}
	});
};

help_pilot_client.poll = function () {
	if (!help_pilot_client.available) {
		return;
	}

	frappe.call({
		method: "help_pilot_client.api.poll_updates",
		// A background check must never freeze the screen or shout on failure.
		freeze: false,
		no_spinner: true,
		callback: ({ message }) => {
			(message && message.events ? message.events : []).forEach(help_pilot_client.alert);
		},
		error: () => {},
	});
};

// ----------------------------------------------------------------------
// Navbar button
// ----------------------------------------------------------------------
$(document).on("toolbar_setup", () => help_pilot_client.mount_button());

$(document).ready(() => {
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
		<li class="nav-item dropdown hp-help-item">
			<button class="hp-help-btn" type="button" title="${__("Help tickets")}">
				<svg class="icon icon-sm"><use href="#icon-help"></use></svg>
				<span>${__("Help")}</span>
			</button>
		</li>
	`);

	$item.find("button").on("click", (e) => {
		e.stopPropagation();
		help_pilot_client.menu($item);
	});
	$nav.prepend($item);

	frappe.call({
		method: "help_pilot_client.api.is_available",
		callback: ({ message }) => {
			help_pilot_client.available = !!message;
			$item.toggleClass("hidden", !message);
			if (message) {
				help_pilot_client.start_polling();
				help_pilot_client.ask_desktop_permission();
				help_pilot_client.poll();
			}
		},
		error: () => $item.addClass("hidden"),
	});
};

// A small menu instead of going straight to the form: people need to find
// their existing tickets far more often than they need to raise a new one.
help_pilot_client.menu = function ($item) {
	$(".hp-help-menu").remove();

	const $menu = $(`
		<div class="hp-help-menu">
			<div class="hp-help-menu-item" data-act="raise">${__("Raise a ticket")}</div>
			<div class="hp-help-menu-item" data-act="mine">${__("My tickets")}</div>
			<div class="hp-help-menu-sep"></div>
			<div class="hp-help-menu-item" data-act="sound">
				${help_pilot_client.sound_enabled() ? __("Turn sound off") : __("Turn sound on")}
			</div>
		</div>
	`).appendTo($item);

	$menu.on("click", ".hp-help-menu-item", function () {
		const act = $(this).data("act");
		$menu.remove();

		if (act === "raise") {
			help_pilot_client.raise_ticket();
		} else if (act === "mine") {
			frappe.set_route("my-help-tickets");
		} else if (act === "sound") {
			const on = !help_pilot_client.sound_enabled();
			help_pilot_client.set_sound(on);
			if (on) {
				help_pilot_client.play("chime");
			}
			frappe.show_alert({
				message: on ? __("Sound on") : __("Sound off"),
				indicator: "blue",
			});
		}
	});

	$(document).one("click", () => $menu.remove());
};

// ----------------------------------------------------------------------
// Raise a ticket
// ----------------------------------------------------------------------
help_pilot_client.raise_ticket = function (on_submit) {
	frappe.call({
		method: "help_pilot_client.api.get_form_options",
		freeze: true,
		freeze_message: __("Loading..."),
		callback: ({ message }) => {
			help_pilot_client.options = message || {};
			const departments = (help_pilot_client.options.departments || []).map((d) => d.name);

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
	const opts = help_pilot_client.options || {};

	const branch_field = opts.has_branch_doctype
		? {
				fieldname: "branch",
				fieldtype: "Link",
				options: "Branch",
				label: __("Which branch are you at?"),
		  }
		: {
				fieldname: "branch",
				fieldtype: "Data",
				label: __("Which branch are you at?"),
		  };

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
				onchange: () => help_pilot_client.load_categories(dialog),
			},
			{
				fieldname: "issue_category",
				fieldtype: "Select",
				label: __("What kind of problem?"),
				options: [""],
			},
			{ fieldtype: "Column Break" },
			branch_field,
			{
				fieldname: "contact_no",
				fieldtype: "Data",
				label: __("Your contact number"),
				default: opts.contact_no || "",
				description: __("So the team can call you if they need to."),
			},
			{ fieldtype: "Section Break" },
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
				description: __("Optional. You can add more after sending."),
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
					help_pilot_client.play("submit");
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
	help_pilot_client.load_categories(dialog);
	setTimeout(() => dialog.get_field("subject").set_focus(), 200);
};

help_pilot_client.load_categories = function (dialog) {
	const department = dialog.get_value("department");
	const field = dialog.get_field("issue_category");

	if (!department) {
		return;
	}

	frappe.call({
		method: "help_pilot_client.api.get_categories",
		args: { department },
		callback: ({ message }) => {
			const rows = message || [];
			// Store the hub's id, show the readable name.
			field.df.options = [""].concat(rows.map((r) => r.name));
			field.refresh();

			const $select = field.$input;
			if ($select && $select.length) {
				rows.forEach((r) => {
					$select.find(`option[value="${r.name}"]`).text(r.category_name);
				});
			}

			// A department with no categories should not show an empty control.
			dialog.set_df_property("issue_category", "hidden", rows.length ? 0 : 1);
		},
	});
};
