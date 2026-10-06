#!/usr/bin/python3
"""Finds the applications which can open a given file type.

The AppStream catalogs (the Debian DEP-11 metadata downloaded by APT, and the
AppStream data of the Flatpak remotes) list the media types each application
can open, taken from the MimeType key of its .desktop file.
"""

import threading

import gi
gi.require_version('AppStream', '1.0')
from gi.repository import AppStream, Gio, GLib

from misc import debug

_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    global _pool

    with _pool_lock:
        if _pool is None:
            pool = AppStream.Pool()
            pool.load(None)
            _pool = pool
        return _pool

def _expand_mime_types(mime_types):
    # Also look for the canonical name of aliased types (application/x-pdf -> application/pdf)
    expanded = []
    for mime_type in mime_types:
        for candidate in (mime_type, Gio.content_type_from_mime_type(mime_type)):
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded

def find_providers(mime_types):
    """
    Returns a list of (pkg_type, name) for the applications which can open any of mime_types:
    ("a", "calibre") for a Debian package, ("f", "org.kde.okular") for a Flatpak.
    Blocking, call it from a thread.
    """
    pool = _get_pool()
    providers = []

    for mime_type in _expand_mime_types(mime_types):
        box = pool.get_components_by_provided_item(AppStream.ProvidedKind.MEDIATYPE, mime_type)
        for i in range(box.get_size()):
            component = box.index_safe(i)

            bundle = component.get_bundle(AppStream.BundleKind.FLATPAK)
            if bundle is not None:
                # app/org.kde.okular/x86_64/stable
                parts = bundle.get_id().split("/")
                if len(parts) < 2 or parts[0] != "app":
                    continue
                provider = ("f", parts[1])
            elif component.get_pkgname():
                provider = ("a", component.get_pkgname())
            else:
                continue

            if provider not in providers:
                providers.append(provider)

    debug("Providers for %s: %s" % (mime_types, providers))
    return providers

def find_providers_async(mime_types, callback):
    """ callback(providers) is called in the main loop """
    def thread_func():
        try:
            providers = find_providers(mime_types)
        except GLib.Error as e:
            print("menta-install: Could not load the AppStream catalogs: %s" % e.message)
            providers = []
        GLib.idle_add(callback, providers)

    thread = threading.Thread(target=thread_func, daemon=True)
    thread.start()
