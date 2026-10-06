#!/usr/bin/python3
"""Settings widgets bound to GSettings keys, for the preferences window.

A page holds sections, a section holds rows. Rows can be revealed
depending on the value of a GSettings key.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gio, Gtk


class SettingsPage(Gtk.Box):
    def __init__(self):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.set_margin_start(32)
        self.set_margin_end(32)
        self.set_margin_top(18)
        self.set_margin_bottom(18)

    def add_section(self, title=None, subtitle=None):
        section = SettingsSection(title, subtitle)
        self.pack_start(section, False, False, 0)
        return section


class SettingsSection(Gtk.Box):
    def __init__(self, title=None, subtitle=None):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, spacing=6)

        if title:
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label = Gtk.Label(xalign=0)
            label.set_markup("<b>%s</b>" % title)
            header.pack_start(label, False, False, 0)
            if subtitle:
                label = Gtk.Label(label=subtitle, xalign=0)
                label.get_style_context().add_class("dim-label")
                header.pack_start(label, False, False, 0)
            self.pack_start(header, False, False, 0)

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add(self.rows)
        self.pack_start(frame, False, False, 0)

    @staticmethod
    def _set_row_margins(widget):
        # Keep margins the caller already set (e.g. the indented refresh grid)
        widget.set_margin_start(widget.get_margin_start() or 12)
        widget.set_margin_end(widget.get_margin_end() or 12)
        widget.set_margin_top(widget.get_margin_top() or 6)
        widget.set_margin_bottom(widget.get_margin_bottom() or 6)

    def add_row(self, widget):
        self._set_row_margins(widget)
        self.rows.pack_start(widget, False, False, 0)
        return widget

    def add_reveal_row(self, widget, schema, key, values=None):
        self._set_row_margins(widget)
        revealer = SettingsRevealer(schema, key, values)
        revealer.add(widget)
        self.rows.pack_start(revealer, False, False, 0)
        return revealer


class SettingsRevealer(Gtk.Revealer):
    """ Shows its child when the key is true, or when its value is in values """

    def __init__(self, schema, key, values=None):
        Gtk.Revealer.__init__(self, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                              transition_duration=150)
        self.key = key
        self.values = values
        self.settings = Gio.Settings(schema_id=schema)
        self.settings.connect("changed::%s" % key, self._on_changed)
        self._on_changed(self.settings, key)

    def _on_changed(self, settings, key):
        value = settings.get_value(key).unpack()
        if self.values is None:
            self.set_reveal_child(bool(value))
        else:
            self.set_reveal_child(value in self.values)


class SettingsWidget(Gtk.Box):
    """ A label on the left and a content_widget on the right """

    def __init__(self, label, content_widget):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.label = Gtk.Label(label=label, xalign=0)
        self.label.set_line_wrap(True)
        self.content_widget = content_widget
        if label:
            self.pack_start(self.label, True, True, 0)
        self.pack_end(self.content_widget, False, False, 0)
        self.content_widget.set_valign(Gtk.Align.CENTER)


class Switch(SettingsWidget):
    def __init__(self, label):
        SettingsWidget.__init__(self, label, Gtk.Switch())


class GSettingsSwitch(Switch):
    def __init__(self, label, schema, key):
        Switch.__init__(self, label)
        self.settings = Gio.Settings(schema_id=schema)
        self.settings.bind(key, self.content_widget, "active", Gio.SettingsBindFlags.DEFAULT)


class GSettingsSpinButton(SettingsWidget):
    def __init__(self, label, schema, key, mini=0, maxi=100, step=1, page=10):
        spin = Gtk.SpinButton.new_with_range(mini, maxi, step)
        spin.set_digits(0)
        spin.set_increments(step, page)
        SettingsWidget.__init__(self, label, spin)
        self.settings = Gio.Settings(schema_id=schema)
        self.settings.bind(key, spin, "value", Gio.SettingsBindFlags.DEFAULT)


class GSettingsComboBox(SettingsWidget):
    """ options is a list of (value, label) pairs for a string key """

    def __init__(self, label, schema, key, options):
        self.model = Gtk.ListStore(str, str)
        for value, option_label in options:
            self.model.append([value, option_label])

        combo = Gtk.ComboBox(model=self.model, id_column=0)
        renderer = Gtk.CellRendererText()
        combo.pack_start(renderer, True)
        combo.add_attribute(renderer, "text", 1)
        SettingsWidget.__init__(self, label, combo)

        self.settings = Gio.Settings(schema_id=schema)
        self.settings.bind(key, combo, "active-id", Gio.SettingsBindFlags.DEFAULT)
