#!/usr/bin/python3

import os
import time

from gi.repository import Gio

DEBUG_MODE = os.getenv("DEBUG", False)

# Used as a decorator to time functions
def print_timing(func):
    if not DEBUG_MODE:
        return func
    else:
        def wrapper(*arg):
            t1 = time.time()
            res = func(*arg)
            t2 = time.time()
            print('%s took %0.3f ms' % (func.__qualname__, (t2 - t1) * 1000.0))
            return res
        return wrapper

def debug(str):
    if not DEBUG_MODE:
        return
    print("menta-install (DEBUG): %s" % str)

def networking_available():
    nm = Gio.NetworkMonitor.get_default()
    return nm.get_connectivity() == Gio.NetworkConnectivity.FULL

class VisibilityGroup:
    """ Shows, hides or sets the sensitivity of several widgets at once """

    def __init__(self, visible, sensitive, widgets):
        self.widgets = list(widgets)
        self.set_visible(visible)
        self.set_sensitive(sensitive)

    def set_visible(self, visible):
        for widget in self.widgets:
            widget.set_visible(visible)

    def show(self):
        self.set_visible(True)

    def hide(self):
        self.set_visible(False)

    def set_sensitive(self, sensitive):
        for widget in self.widgets:
            widget.set_sensitive(sensitive)

def add_network_proxy_to_env():
    # Export the desktop's proxy settings (set by mate-network-properties
    # or gnome-control-center in org.gnome.system.proxy) so that urllib,
    # pycurl and apt pick them up.
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.system.proxy", True) is None:
        return
    settings = Gio.Settings(schema_id="org.gnome.system.proxy")
    if settings.get_string("mode") != "manual":
        return

    def proxy_url(scheme, use_auth=False):
        proxy = Gio.Settings(schema_id="org.gnome.system.proxy.%s" % scheme)
        host = proxy.get_string("host")
        port = proxy.get_int("port")
        if not host or port == 0:
            return None
        auth = ""
        if use_auth and proxy.get_boolean("use-authentication"):
            user = proxy.get_string("authentication-user")
            password = proxy.get_string("authentication-password")
            if user:
                auth = "%s:%s@" % (user, password) if password else "%s@" % user
        return "http://%s%s:%d/" % (auth, host, port)

    http = proxy_url("http", use_auth=True)
    https = proxy_url("https")
    if settings.get_boolean("use-same-proxy"):
        https = https or http
    ftp = proxy_url("ftp")
    for name, value in (("http_proxy", http), ("https_proxy", https), ("ftp_proxy", ftp)):
        if value is not None:
            os.environ[name] = value
            os.environ[name.upper()] = value
    ignore_hosts = settings.get_strv("ignore-hosts")
    if ignore_hosts:
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = ",".join(ignore_hosts)
