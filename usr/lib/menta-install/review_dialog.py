#!/usr/bin/python3

import gettext

from gi.repository import Gtk, GLib

_ = gettext.gettext

# Limits used by the Open Desktop Ratings Service clients
SUMMARY_MAX_LENGTH = 70
DESCRIPTION_MAX_LENGTH = 3000

class WriteReviewDialog(Gtk.Dialog):
    """ Asks the user for a rating, a summary and a description """

    def __init__(self, parent, app_name):
        Gtk.Dialog.__init__(self, transient_for=parent, modal=True, destroy_with_parent=True)
        self.set_title(_("Review %s") % app_name)
        self.set_default_size(480, -1)
        self.set_border_width(6)
        self.set_icon_name("menta-install")

        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self.submit_button = self.add_button(_("_Submit"), Gtk.ResponseType.OK)
        self.submit_button.get_style_context().add_class("suggested-action")
        self.submit_button.set_sensitive(False)

        self.rating = 0

        grid = Gtk.Grid(row_spacing=12, column_spacing=12, border_width=6)
        self.get_content_area().pack_start(grid, True, True, 0)

        row = 0
        label = Gtk.Label(label=_("Rating"), xalign=1.0)
        label.get_style_context().add_class("dim-label")
        grid.attach(label, 0, row, 1, 1)
        stars_box = Gtk.Box(spacing=2)
        self.star_buttons = []
        for i in range(5):
            button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            button.add(Gtk.Image.new_from_icon_name("non-starred-symbolic", Gtk.IconSize.LARGE_TOOLBAR))
            button.connect("clicked", self.on_star_clicked, i + 1)
            stars_box.pack_start(button, False, False, 0)
            self.star_buttons.append(button)
        grid.attach(stars_box, 1, row, 1, 1)

        row += 1
        label = Gtk.Label(label=_("Your name"), xalign=1.0)
        label.get_style_context().add_class("dim-label")
        grid.attach(label, 0, row, 1, 1)
        self.name_entry = Gtk.Entry(hexpand=True, activates_default=True)
        real_name = GLib.get_real_name()
        if real_name in (None, "", "Unknown"):
            real_name = GLib.get_user_name()
        self.name_entry.set_text(real_name)
        grid.attach(self.name_entry, 1, row, 1, 1)

        row += 1
        label = Gtk.Label(label=_("Summary"), xalign=1.0)
        label.get_style_context().add_class("dim-label")
        grid.attach(label, 0, row, 1, 1)
        self.summary_entry = Gtk.Entry(hexpand=True, max_length=SUMMARY_MAX_LENGTH, activates_default=True)
        self.summary_entry.set_placeholder_text(_("Give your review a title"))
        self.summary_entry.connect("changed", self.update_submit_button)
        grid.attach(self.summary_entry, 1, row, 1, 1)

        row += 1
        label = Gtk.Label(label=_("Review"), xalign=1.0, yalign=0.0)
        label.get_style_context().add_class("dim-label")
        grid.attach(label, 0, row, 1, 1)
        self.description_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.description_view.set_left_margin(6)
        self.description_view.set_right_margin(6)
        self.description_view.set_top_margin(6)
        self.description_view.set_bottom_margin(6)
        self.description_view.get_buffer().connect("changed", self.update_submit_button)
        scrolled = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, min_content_height=140, hexpand=True, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.description_view)
        grid.attach(scrolled, 1, row, 1, 1)

        row += 1
        notice = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=50)
        notice.set_markup("<small>%s</small>" % GLib.markup_escape_text(
            _("Your review will be published on the Open Desktop Ratings Service (odrs.gnome.org) under the name above, "
              "and will be visible to the users of any Linux distribution.")))
        notice.get_style_context().add_class("dim-label")
        grid.attach(notice, 0, row, 2, 1)

        self.set_default_response(Gtk.ResponseType.OK)
        self.get_content_area().show_all()

    def on_star_clicked(self, button, rating):
        self.rating = rating
        for i, star_button in enumerate(self.star_buttons):
            icon_name = "starred-symbolic" if i < rating else "non-starred-symbolic"
            star_button.get_child().set_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        self.update_submit_button()

    def get_description(self):
        buf = self.description_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

    def update_submit_button(self, *args):
        description = self.get_description()
        valid = self.rating > 0 \
                and self.summary_entry.get_text().strip() != "" \
                and description != "" \
                and len(description) <= DESCRIPTION_MAX_LENGTH
        self.submit_button.set_sensitive(valid)

    def get_review(self):
        """ Returns (user_display, rating, summary, description) """
        name = self.name_entry.get_text().strip()
        if name == "":
            name = _("Anonymous")
        return (name, self.rating, self.summary_entry.get_text().strip(), self.get_description())
