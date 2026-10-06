#!/usr/bin/python3
"""PackageKit session service for menta-install.

Applications ask the session D-Bus service org.freedesktop.PackageKit to
install software. Caja, for example, offers to search for an application when
a file type has none, and calls InstallMimeTypes. This service hands these
requests over to menta-install:

  InstallMimeTypes     -> menta-install search-mime <types>
  InstallPackageFiles  -> menta-install install <file.deb>
  InstallPackageNames  -> menta-install show <package>

It is started by D-Bus on demand and exits when idle.
"""

import os
import subprocess
import sys

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.PackageKit"
OBJECT_PATH = "/org/freedesktop/PackageKit"
IDLE_TIMEOUT = 120
MENTA_INSTALL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "menta_install.py")
NOT_SUPPORTED_ERROR = "org.freedesktop.PackageKit.Modify.Failed"

def _methods(with_xid):
    # Modify takes a window XID first, Modify2 a desktop ID and platform data last
    xid = "<arg type='u' name='xid' direction='in'/>" if with_xid else ""
    extra = "" if with_xid else ("<arg type='s' name='desktop_id' direction='in'/>"
                                 "<arg type='a{sv}' name='platform_data' direction='in'/>")
    methods = ""
    for name, arg in (("InstallPackageFiles", "files"), ("InstallProvideFiles", "files"),
                      ("InstallPackageNames", "packages"), ("InstallMimeTypes", "mime_types"),
                      ("InstallFontconfigResources", "resources"), ("InstallGStreamerResources", "resources"),
                      ("RemovePackageByFiles", "files"), ("InstallPrinterDrivers", "resources")):
        methods += ("<method name='%s'>%s<arg type='as' name='%s' direction='in'/>"
                    "<arg type='s' name='interaction' direction='in'/>%s</method>" % (name, xid, arg, extra))
    methods += ("<method name='InstallResources'>%s<arg type='s' name='type' direction='in'/>"
                "<arg type='as' name='resources' direction='in'/>"
                "<arg type='s' name='interaction' direction='in'/>%s</method>" % (xid, extra))
    return methods

INTROSPECTION_XML = """
<node>
  <interface name='org.freedesktop.PackageKit.Query'>
    <method name='IsInstalled'>
      <arg type='s' name='package_name' direction='in'/>
      <arg type='s' name='interaction' direction='in'/>
      <arg type='b' name='installed' direction='out'/>
    </method>
    <method name='SearchFile'>
      <arg type='s' name='file_name' direction='in'/>
      <arg type='s' name='interaction' direction='in'/>
      <arg type='b' name='installed' direction='out'/>
      <arg type='s' name='package_name' direction='out'/>
    </method>
  </interface>
  <interface name='org.freedesktop.PackageKit.Modify'>%s</interface>
  <interface name='org.freedesktop.PackageKit.Modify2'>%s</interface>
</node>
""" % (_methods(True), _methods(False))

class PackageKitSessionService:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.idle_id = 0
        self.node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self.owner_id = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                         self.on_bus_acquired, None, self.on_name_lost)
        self.reset_idle_timer()

    def run(self):
        self.loop.run()

    def reset_idle_timer(self):
        if self.idle_id > 0:
            GLib.source_remove(self.idle_id)
        self.idle_id = GLib.timeout_add_seconds(IDLE_TIMEOUT, self.on_idle_timeout)

    def on_idle_timeout(self):
        self.idle_id = 0
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def on_bus_acquired(self, connection, name):
        for interface in self.node_info.interfaces:
            connection.register_object(OBJECT_PATH, interface, self.on_method_call, None, None)

    def on_name_lost(self, connection, name):
        print("menta-install-pk-session: Could not own %s, another software manager provides it" % name,
              file=sys.stderr)
        self.loop.quit()

    def launch(self, args):
        subprocess.Popen([MENTA_INSTALL] + args, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def on_method_call(self, connection, sender, object_path, interface_name, method_name,
                       parameters, invocation):
        self.reset_idle_timer()
        params = parameters.unpack()

        if interface_name == "org.freedesktop.PackageKit.Query":
            if method_name == "IsInstalled":
                invocation.return_value(GLib.Variant("(b)", (self.is_installed(params[0]),)))
            else:
                installed, package = self.search_file(params[0])
                invocation.return_value(GLib.Variant("(bs)", (installed, package)))
            return

        # Modify: (xid, items, interaction), Modify2: (items, interaction, desktop_id, platform_data)
        if method_name == "InstallResources":
            items = params[2] if interface_name.endswith("Modify") else params[1]
        else:
            items = params[1] if interface_name.endswith("Modify") else params[0]
        items = [item for item in items if item]

        if not items:
            invocation.return_dbus_error(NOT_SUPPORTED_ERROR, "Nothing to install")
            return

        if method_name == "InstallMimeTypes":
            self.launch(["search-mime"] + items)
        elif method_name == "InstallPackageFiles":
            for path in items:
                self.launch(["install", path])
        elif method_name == "InstallPackageNames":
            self.launch(["show", items[0]])
        else:
            invocation.return_dbus_error(NOT_SUPPORTED_ERROR, "%s is not supported by menta-install" % method_name)
            return

        invocation.return_value(None)

    def is_installed(self, package_name):
        try:
            output = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", package_name],
                                    capture_output=True, text=True, timeout=10).stdout
            return output.strip() == "installed"
        except Exception:
            return False

    def search_file(self, file_name):
        try:
            output = subprocess.run(["dpkg-query", "-S", file_name],
                                    capture_output=True, text=True, timeout=10).stdout
            for line in output.splitlines():
                package = line.split(":")[0].split(",")[0].strip()
                if package and not line.startswith("diversion"):
                    return True, package
        except Exception:
            pass
        return False, ""

if __name__ == "__main__":
    PackageKitSessionService().run()
