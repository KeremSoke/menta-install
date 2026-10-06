#!/usr/bin/python3
"""A window to install a local .deb file, in the spirit of GDebi.

The package and its dependencies are checked with python3-apt, and installed
or removed with PackageKit, which resolves the dependencies from the
configured repositories.
"""

import gettext
import os
import threading

import apt
import apt.debfile
import apt_pkg

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PackageKitGlib', '1.0')
from gi.repository import Gtk, Gio, GLib, Pango
from gi.repository import PackageKitGlib as packagekit

from installer import dialogs
from installer.pkgInfo import get_dep11_icon

_ = gettext.gettext

DEB_MIME_TYPES = ("application/vnd.debian.binary-package", "application/x-deb")

def is_deb_file(path):
    if path.lower().endswith(".deb"):
        return True
    try:
        info = Gio.File.new_for_path(path).query_info(Gio.FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE,
                                                      Gio.FileQueryInfoFlags.NONE, None)
        return Gio.content_type_get_mime_type(info.get_content_type()) in DEB_MIME_TYPES
    except GLib.Error:
        return False

STATUS_LABELS = {
    packagekit.StatusEnum.WAIT: _("Waiting"),
    packagekit.StatusEnum.WAITING_FOR_LOCK: _("Waiting for other software managers to finish"),
    packagekit.StatusEnum.WAITING_FOR_AUTH: _("Waiting for authentication"),
    packagekit.StatusEnum.SETUP: _("Preparing"),
    packagekit.StatusEnum.LOADING_CACHE: _("Loading the list of packages"),
    packagekit.StatusEnum.QUERY: _("Resolving dependencies"),
    packagekit.StatusEnum.DEP_RESOLVE: _("Resolving dependencies"),
    packagekit.StatusEnum.DOWNLOAD: _("Downloading"),
    packagekit.StatusEnum.SIG_CHECK: _("Checking signatures"),
    packagekit.StatusEnum.TEST_COMMIT: _("Testing changes"),
    packagekit.StatusEnum.COMMIT: _("Applying changes"),
    packagekit.StatusEnum.INSTALL: _("Installing"),
    packagekit.StatusEnum.REMOVE: _("Removing"),
    packagekit.StatusEnum.UPDATE: _("Upgrading"),
    packagekit.StatusEnum.CLEANUP: _("Cleaning up"),
    packagekit.StatusEnum.FINISHED: _("Finished"),
}

class DebTask(packagekit.Task):
    """ PackageKit task which doesn't ask questions: the window already showed the changes """

    def __init__(self):
        packagekit.Task.__init__(self)
        self.set_simulate(False)
        self.set_interactive(True)

    def do_untrusted_question(self, request, results):
        # Local package files are never signed. The user chose to install this file.
        self.user_accepted(request)

    def do_simulate_question(self, request, results):
        self.user_accepted(request)

    def do_key_question(self, request, results):
        self.user_declined(request)

    def do_eula_question(self, request, results):
        self.user_declined(request)

    def do_media_change_question(self, request, results):
        self.user_declined(request)

    def do_repair_question(self, request, results):
        self.user_declined(request)

class DebInstallerWindow(Gtk.Window):
    def __init__(self, application, path):
        Gtk.Window.__init__(self, application=application)
        self.path = path
        self.deb = None
        self.busy = False
        self.cancellable = None
        self.installed_version = None
        self.pending_message = None

        self.set_title(_("Package Installer - %s") % os.path.basename(path))
        self.set_icon_name("menta-install")
        self.set_default_size(620, 520)
        self.connect("delete-event", self.on_delete_event)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, border_width=12)
        self.add(vbox)

        # Header: icon, name and summary, action buttons
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vbox.pack_start(header, False, False, 0)

        self.icon = Gtk.Image.new_from_icon_name("package-x-generic", Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(64)
        self.icon.set_valign(Gtk.Align.START)
        header.pack_start(self.icon, False, False, 0)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        header.pack_start(titles, True, True, 0)

        self.name_label = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self.name_label.set_markup("<big><b>%s</b></big>" % GLib.markup_escape_text(os.path.basename(path)))
        titles.pack_start(self.name_label, False, False, 0)

        self.summary_label = Gtk.Label(xalign=0.0, wrap=True)
        self.summary_label.get_style_context().add_class("dim-label")
        titles.pack_start(self.summary_label, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        header.pack_end(buttons, False, False, 0)

        self.install_button = Gtk.Button(label=_("Install Package"), sensitive=False)
        self.install_button.get_style_context().add_class("suggested-action")
        self.install_button.connect("clicked", self.on_install_clicked)
        buttons.pack_start(self.install_button, False, False, 0)

        self.remove_button = Gtk.Button(label=_("Remove Package"), no_show_all=True)
        self.remove_button.get_style_context().add_class("destructive-action")
        self.remove_button.connect("clicked", self.on_remove_clicked)
        buttons.pack_start(self.remove_button, False, False, 0)

        # Status: result of the dependency check
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vbox.pack_start(status_box, False, False, 0)
        self.status_spinner = Gtk.Spinner(active=True)
        status_box.pack_start(self.status_spinner, False, False, 0)
        self.status_label = Gtk.Label(xalign=0.0, wrap=True)
        self.status_label.set_markup("<b>%s</b>" % _("Checking the package..."))
        status_box.pack_start(self.status_label, True, True, 0)

        self.changes_expander = Gtk.Expander(no_show_all=True)
        self.changes_label = Gtk.Label(xalign=0.0, wrap=True, selectable=True, margin_start=12, margin_top=6)
        self.changes_expander.add(self.changes_label)
        self.changes_label.show()
        vbox.pack_start(self.changes_expander, False, False, 0)

        # Progress, while installing or removing
        self.progress_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.progress_label = Gtk.Label(xalign=0.0)
        self.progress_bar = Gtk.ProgressBar()
        progress_box.pack_start(self.progress_label, False, False, 0)
        progress_box.pack_start(self.progress_bar, False, False, 0)
        self.progress_revealer.add(progress_box)
        vbox.pack_start(self.progress_revealer, False, False, 0)

        # Package information
        notebook = Gtk.Notebook()
        vbox.pack_start(notebook, True, True, 0)

        self.description_label = Gtk.Label(xalign=0.0, yalign=0.0, wrap=True, selectable=True, margin=12)
        notebook.append_page(self._scrolled(self.description_label), Gtk.Label(label=_("Description")))

        self.details_grid = Gtk.Grid(row_spacing=6, column_spacing=12, border_width=12)
        notebook.append_page(self._scrolled(self.details_grid), Gtk.Label(label=_("Details")))

        self.files_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                       left_margin=12, top_margin=12, bottom_margin=12)
        notebook.append_page(self._scrolled(self.files_view), Gtk.Label(label=_("Included Files")))

        self.show_all()

        self.check_package()

    def _scrolled(self, child):
        scrolled = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.NONE)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(child)
        return scrolled

    # Checking the package

    def check_package(self):
        self.install_button.set_sensitive(False)
        self.remove_button.hide()
        self.status_spinner.show()
        self.status_spinner.start()
        thread = threading.Thread(target=self._check_package_thread, daemon=True)
        thread.start()

    def _check_package_thread(self):
        result = {}
        try:
            cache = apt.Cache()
            deb = apt.debfile.DebPackage(self.path, cache)

            result["deb"] = deb
            result["ok"] = deb.check(allow_downgrade=True)
            result["failure"] = deb._failure_string
            if result["ok"]:
                result["install"], result["remove"], unused = deb.required_changes
            else:
                result["install"], result["remove"] = [], []

            result["installed_version"] = None
            name = deb.pkgname
            arch = deb._sections.get("Architecture", "all")
            for key in ("%s:%s" % (name, arch), name):
                if key in cache and cache[key].installed is not None:
                    result["installed_version"] = cache[key].installed.version
                    break

            result["files"] = deb.filelist
        except Exception as e:
            result["error"] = str(e)

        GLib.idle_add(self._on_package_checked, result)

    def _on_package_checked(self, result):
        self.status_spinner.stop()
        self.status_spinner.hide()

        if "error" in result:
            self._set_status(_("This file could not be opened as a package: %s") % result["error"], error=True)
            return GLib.SOURCE_REMOVE

        deb = result["deb"]
        self.deb = deb
        sections = deb._sections
        name = deb.pkgname
        version = sections.get("Version", "")
        self.installed_version = result["installed_version"]

        description = sections.get("Description", "")
        summary, unused, long_description = description.partition("\n")
        self.name_label.set_markup("<big><b>%s</b></big>" % GLib.markup_escape_text(name))
        self.summary_label.set_text(summary.strip())
        self.description_label.set_text(self._format_description(long_description) or summary.strip())

        icon = get_dep11_icon(name, 64)
        if icon is not None:
            self.icon.set_from_file(icon)
        elif Gtk.IconTheme.get_default().has_icon(name):
            self.icon.set_from_icon_name(name, Gtk.IconSize.DIALOG)
            self.icon.set_pixel_size(64)

        self._fill_details(sections)
        self.files_view.get_buffer().set_text("\n".join(sorted("/" + f.lstrip("./") for f in result["files"] if f.strip("./"))))

        if not result["ok"]:
            self._set_status(_("Error: %s") % result["failure"], error=True)
            return GLib.SOURCE_REMOVE

        if self.installed_version is None:
            action = _("Install Package")
            status = _("Ready to install")
        else:
            comparison = apt_pkg.version_compare(version, self.installed_version)
            if comparison == 0:
                action = _("Reinstall Package")
                status = _("The same version is already installed")
            elif comparison > 0:
                action = _("Upgrade Package")
                status = _("An older version is installed (%s)") % self.installed_version
            else:
                action = _("Downgrade Package")
                status = _("A newer version is already installed (%s)") % self.installed_version
            self.remove_button.show()

        self.install_button.set_label(action)
        self.install_button.set_sensitive(True)
        if self.pending_message is not None:
            status = self.pending_message
            self.pending_message = None
        self._set_status(status)
        self._show_required_changes(result["install"], result["remove"])

        return GLib.SOURCE_REMOVE

    def _format_description(self, text):
        # Debian control format: lines start with a space, " ." is an empty line
        lines = []
        for line in text.split("\n"):
            line = line.strip()
            lines.append("" if line == "." else line)
        return "\n".join(lines).strip()

    def _fill_details(self, sections):
        for child in self.details_grid.get_children():
            self.details_grid.remove(child)

        rows = [
            (_("Package"), sections.get("Package", "")),
            (_("Version"), sections.get("Version", "")),
            (_("Installed version"), self.installed_version or _("Not installed")),
            (_("Architecture"), sections.get("Architecture", "")),
            (_("Maintainer"), sections.get("Maintainer", "")),
            (_("Section"), sections.get("Section", "")),
            (_("Priority"), sections.get("Priority", "")),
        ]
        installed_size = sections.get("Installed-Size", "")
        if installed_size.isdigit():
            rows.append((_("Installed size"), GLib.format_size(int(installed_size) * 1024)))
        rows.append((_("File size"), GLib.format_size(os.path.getsize(self.path))))
        for field, label in (("Depends", _("Depends")), ("Recommends", _("Recommends")),
                             ("Conflicts", _("Conflicts")), ("Replaces", _("Replaces")),
                             ("Provides", _("Provides"))):
            rows.append((label, sections.get(field, "")))

        row = 0
        for label, value in rows:
            if not value:
                continue
            key = Gtk.Label(label=label, xalign=1.0, yalign=0.0)
            key.get_style_context().add_class("dim-label")
            val = Gtk.Label(label=value, xalign=0.0, wrap=True, selectable=True, hexpand=True)
            self.details_grid.attach(key, 0, row, 1, 1)
            self.details_grid.attach(val, 1, row, 1, 1)
            row += 1

        homepage = sections.get("Homepage", "")
        if homepage:
            key = Gtk.Label(label=_("Homepage"), xalign=1.0)
            key.get_style_context().add_class("dim-label")
            link = Gtk.Label(xalign=0.0)
            link.set_markup("<a href='%s'>%s</a>" % (GLib.markup_escape_text(homepage), GLib.markup_escape_text(homepage)))
            self.details_grid.attach(key, 0, row, 1, 1)
            self.details_grid.attach(link, 1, row, 1, 1)

        self.details_grid.show_all()

    def _show_required_changes(self, to_install, to_remove):
        to_install = [name for name in to_install if name != self.deb.pkgname]
        if not to_install and not to_remove:
            self.changes_expander.hide()
            return

        parts = []
        if to_install:
            parts.append(gettext.ngettext("Requires the installation of %d additional package",
                                          "Requires the installation of %d additional packages",
                                          len(to_install)) % len(to_install))
        if to_remove:
            parts.append(gettext.ngettext("requires the removal of %d package",
                                          "requires the removal of %d packages",
                                          len(to_remove)) % len(to_remove))
        self.changes_expander.set_label(", ".join(parts) + ".")

        text = ""
        if to_install:
            text += "<b>%s</b>\n%s\n" % (_("To install:"), GLib.markup_escape_text(", ".join(sorted(to_install))))
        if to_remove:
            text += "<b>%s</b>\n%s\n" % (_("To remove:"), GLib.markup_escape_text(", ".join(sorted(to_remove))))
        self.changes_label.set_markup(text.strip())
        self.changes_expander.show()

    def _set_status(self, text, error=False):
        text = GLib.markup_escape_text(text)
        if error:
            self.status_label.set_markup("<b><span foreground='#d9514a'>%s</span></b>" % text)
        else:
            self.status_label.set_markup("<b>%s</b>" % text)

    # Installing and removing

    def on_install_clicked(self, button):
        if self.deb is None:
            return

        if self.installed_version is not None and \
                apt_pkg.version_compare(self.deb._sections.get("Version", ""), self.installed_version) < 0:
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                       buttons=Gtk.ButtonsType.OK_CANCEL,
                                       text=_("Install an older version?"))
            dialog.format_secondary_text(_("A newer version of this package is already installed. "
                                           "Downgrading can break the software that depends on it."))
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return

        self._run_transaction(self._install_thread, _("Installing"))

    def on_remove_clicked(self, button):
        self._run_transaction(self._remove_thread, _("Removing"))

    def _run_transaction(self, thread_func, label):
        self.busy = True
        self.cancellable = Gio.Cancellable()
        self.install_button.set_sensitive(False)
        self.remove_button.set_sensitive(False)
        self.progress_label.set_text(label)
        self.progress_bar.set_fraction(0.0)
        self.progress_revealer.set_reveal_child(True)

        thread = threading.Thread(target=thread_func, daemon=True)
        thread.start()

    def _install_thread(self):
        task = DebTask()
        error = None
        try:
            task.install_files_sync([self.path], self.cancellable, self._on_progress, None)
        except GLib.Error as e:
            error = e
        GLib.idle_add(self._on_transaction_done, error, _("The package was installed."))

    def _remove_thread(self):
        task = DebTask()
        error = None
        try:
            filters = packagekit.filter_bitfield_from_string("installed")
            results = task.resolve_sync(filters, [self.deb.pkgname], self.cancellable, self._on_progress, None)
            package_ids = [pkg.get_id() for pkg in results.get_package_array()]
            if not package_ids:
                raise GLib.Error(_("The package is not installed."))
            task.remove_packages_sync(package_ids, True, False, self.cancellable, self._on_progress, None)
        except GLib.Error as e:
            error = e
        GLib.idle_add(self._on_transaction_done, error, _("The package was removed."))

    def _on_progress(self, progress, progress_type, data=None):
        if progress_type == packagekit.ProgressType.PERCENTAGE:
            GLib.idle_add(self._update_progress_bar, progress.get_percentage())
        elif progress_type == packagekit.ProgressType.STATUS:
            label = STATUS_LABELS.get(progress.get_status())
            if label is not None:
                GLib.idle_add(self.progress_label.set_text, label)

    def _update_progress_bar(self, percentage):
        if 0 <= percentage <= 100:
            self.progress_bar.set_fraction(percentage / 100.0)
        else:
            self.progress_bar.pulse()
        return GLib.SOURCE_REMOVE

    def _on_transaction_done(self, error, success_message):
        self.busy = False
        self.cancellable = None
        self.progress_revealer.set_reveal_child(False)
        self.install_button.set_sensitive(True)
        self.remove_button.set_sensitive(True)

        if error is None:
            # Shown once the package has been checked again
            self.pending_message = success_message
            self.check_package()
            return GLib.SOURCE_REMOVE

        # The user cancelled the authentication: nothing to report
        code = error.code - 0xFF if error.code >= 0xFF else None
        if code in (packagekit.ErrorEnum.NOT_AUTHORIZED, packagekit.ErrorEnum.TRANSACTION_CANCELLED):
            return GLib.SOURCE_REMOVE

        dialogs.show_error(error.message, self)
        return GLib.SOURCE_REMOVE

    def on_delete_event(self, window, event):
        if not self.busy:
            return False

        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.YES_NO,
                                   text=_("The package is still being installed or removed.\nAre you sure you want to quit?"))
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            if self.cancellable is not None:
                self.cancellable.cancel()
            return False
        return True
