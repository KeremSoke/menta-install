"""Ratings and reviews from the Open Desktop Ratings Service (ODRS).

ODRS (https://odrs.gnome.org) is the distribution-neutral review service also
used by GNOME Software and KDE Discover. Reviews are keyed by AppStream ID
(org.gimp.GIMP, gimp.desktop...), so an app's ratings are gathered from all
the IDs it is known by: its Flatpak ID and the .desktop ID of its Debian package.
"""

import os
import time
import json
import getpass
import gettext
import hashlib
import locale
import threading
from pathlib import Path

import requests

from gi.repository import GLib, GObject

from misc import print_timing

_ = gettext.gettext

ODRS_API_URL = "https://odrs.gnome.org/1.0/reviews/api"
RATINGS_CACHE = os.path.join(GLib.get_user_cache_dir(), "menta-install", "ratings.json")
RATINGS_MAX_AGE = 24 * 60 * 60

# How many reviews to show on the details page
MAX_REVIEWS = 20

def _get_os_name():
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("NAME="):
                        return line.split("=", 1)[1].strip().strip('"\'')
        except OSError:
            continue
    return "Debian"

def _get_user_hash():
    # An anonymous, stable ID for this user on this machine. ODRS uses it to
    # remember which reviews the user wrote or voted on.
    try:
        with open("/etc/machine-id", encoding="utf-8") as f:
            machine_id = f.read().strip()
    except OSError:
        machine_id = ""
    salted = "menta-install[%s:%s]" % (machine_id, getpass.getuser())
    return hashlib.sha1(salted.encode("utf-8")).hexdigest()

def _get_locale():
    try:
        loc = locale.getlocale(locale.LC_MESSAGES)[0]
    except (ValueError, AttributeError):
        loc = None
    return loc or "en_US"

DISTRO = _get_os_name()
USER_HASH = _get_user_hash()

class Review(object):
    def __init__(self, app_id, date, username, rating, summary, comment, review_id=0):
        self.app_id = app_id
        self.date = date
        self.username = username
        # 1 to 5 stars
        self.rating = max(1, min(5, int(rating)))
        self.summary = summary
        self.comment = comment
        self.review_id = review_id

    @classmethod
    def from_odrs(cls, data):
        # ODRS ratings go from 0 to 100, in steps of 20
        rating = round(int(data.get("rating", 0)) / 20)
        return cls(data.get("app_id", ""),
                   float(data.get("date_created", 0)),
                   data.get("user_display") or _("Anonymous"),
                   rating,
                   (data.get("summary") or "").strip(),
                   (data.get("description") or "").strip(),
                   data.get("review_id", 0))

class ReviewInfo:
    def __init__(self, name, app_ids=None):
        self.name = name
        # All the ODRS IDs this app is known by, the most reviewed first
        self.app_ids = app_ids if app_ids else []
        self.reviews = []
        # Number of 1 to 5 stars ratings
        self.stars = [0, 0, 0, 0, 0]
        self.score = 0
        self.avg_rating = 0
        self.num_reviews = 0

    def add_ratings(self, ratings):
        for i in range(5):
            self.stars[i] += int(ratings.get("star%d" % (i + 1), 0))

    def update_stats(self):
        self.num_reviews = sum(self.stars)
        self.avg_rating = 0
        if self.num_reviews > 0:
            sum_rating = sum((i + 1) * count for i, count in enumerate(self.stars))
            self.avg_rating = round(float(sum_rating) / float(self.num_reviews), 1)
            # establish a score based on a 10 votes sample
            significant_votes = min(10, self.num_reviews)
            missing_votes = 10 - significant_votes
            # assume votes voted like the avg, and missing votes vote 2.5 stars.
            self.score = round((self.avg_rating*significant_votes+2.5*missing_votes)/10, 1)
        else:
            self.score = 0

class ReviewCache(GObject.Object):
    __gsignals__ = {
        'reviews-updated': (GObject.SignalFlags.RUN_LAST, None, ()),
    }
    @print_timing
    def __init__(self, apt_flatpak_equivs=None):
        GObject.Object.__init__(self)

        self._cache_lock = threading.Lock()

        # deb name -> flatpak ID, and the other way around
        self._flatpak_equivs = apt_flatpak_equivs if apt_flatpak_equivs else {}
        self._deb_equivs = dict((v, k) for k, v in self._flatpak_equivs.items())

        self._ratings, age = self._load_cache()
        self._infos = {}

        self._killed = False

        if age > RATINGS_MAX_AGE:
            self._update_cache()

    def kill(self):
        self._killed = True

    def keys(self):
        with self._cache_lock:
            return self._ratings.keys()

    def _get_app_ids(self, name):
        names = [name]
        if name in self._flatpak_equivs:
            names.append(self._flatpak_equivs[name])
        if name in self._deb_equivs:
            names.append(self._deb_equivs[name])

        app_ids = []
        for n in names:
            for app_id in (n, "%s.desktop" % n):
                if app_id not in app_ids:
                    app_ids.append(app_id)
        return app_ids

    def __getitem__(self, name):
        with self._cache_lock:
            try:
                return self._infos[name]
            except KeyError:
                pass

            known_ids = [app_id for app_id in self._get_app_ids(name) if app_id in self._ratings]
            known_ids.sort(key=lambda app_id: self._ratings[app_id].get("total", 0), reverse=True)

            info = ReviewInfo(name, known_ids)
            for app_id in known_ids:
                info.add_ratings(self._ratings[app_id])
            info.update_stats()

            if not info.app_ids:
                # Not reviewed yet, but new reviews need an ID to be filed under:
                # the Flatpak ID (org.foo.Bar) or the .desktop ID of a Debian package.
                if name.count(".") >= 2:
                    info.app_ids = [name]
                else:
                    info.app_ids = [self._flatpak_equivs.get(name, "%s.desktop" % name)]

            self._infos[name] = info
            return info

    def __contains__(self, name):
        with self._cache_lock:
            return any(app_id in self._ratings for app_id in self._get_app_ids(name))

    def __len__(self):
        with self._cache_lock:
            return len(self._ratings)

    def _load_cache(self):
        try:
            path = Path(RATINGS_CACHE)
            with path.open(mode='r', encoding="utf8") as f:
                ratings = json.load(f)
            age = time.time() - path.stat().st_mtime
            return ratings, age
        except FileNotFoundError:
            pass
        except Exception as e:
            print("menta-install: Cannot open ratings cache: %s" % str(e))

        return {}, RATINGS_MAX_AGE + 1

    def _update_cache(self):
        thread = threading.Thread(target=self._update_ratings_thread, daemon=True)
        thread.start()

    @print_timing
    def _update_ratings_thread(self):
        try:
            r = requests.get("%s/ratings" % ODRS_API_URL, timeout=30)
            r.raise_for_status()
            ratings = r.json()
        except Exception as e:
            print("menta-install: Could not download ratings: %s" % str(e))
            return

        if self._killed:
            return

        try:
            path = Path(RATINGS_CACHE)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            with tmp_path.open(mode='w', encoding="utf8") as f:
                json.dump(ratings, f)
            tmp_path.replace(path)
        except Exception as e:
            print("menta-install: Could not save ratings cache: %s" % str(e))

        with self._cache_lock:
            self._ratings = ratings
            self._infos = {}

        print("menta-install: Downloaded new ratings")
        GLib.idle_add(self.emit_reviews_updated)

    def emit_reviews_updated(self, data=None):
        if not self._killed:
            self.emit("reviews-updated")
        return GLib.SOURCE_REMOVE

    def fetch_reviews(self, review_info, version, callback):
        """
        Downloads the reviews of an app in a thread. callback(review_info, reviews, user_skey)
        is called in the main loop. user_skey is needed to submit a review, it's None on failure.
        """
        thread = threading.Thread(target=self._fetch_reviews_thread, args=(review_info, version, callback), daemon=True)
        thread.start()

    def _fetch_reviews_thread(self, review_info, version, callback):
        reviews = []
        user_skey = None

        app_ids = review_info.app_ids
        request = {
            "user_hash": USER_HASH,
            "app_id": app_ids[0],
            "locale": _get_locale(),
            "distro": DISTRO,
            "version": version or "unknown",
            "limit": MAX_REVIEWS,
        }
        if len(app_ids) > 1:
            request["compat_ids"] = app_ids[1:]

        try:
            r = requests.post("%s/fetch" % ODRS_API_URL, json=request, timeout=15)
            r.raise_for_status()
            for item in r.json():
                if user_skey is None:
                    user_skey = item.get("user_skey")
                # When an app has no reviews, ODRS returns a single item with only the user key.
                if "review_id" not in item or item.get("reported", 0) > 0:
                    continue
                reviews.append(Review.from_odrs(item))
        except Exception as e:
            print("menta-install: Could not download reviews for %s: %s" % (review_info.name, str(e)))

        reviews.sort(key=lambda x: x.date, reverse=True)
        GLib.idle_add(callback, review_info, reviews, user_skey)

    def submit_review(self, review_info, user_skey, version, user_display, rating, summary, description, callback):
        """
        Submits a review in a thread. callback(success, error_message) is called in the main loop.
        rating goes from 1 to 5 stars.
        """
        request = {
            "user_hash": USER_HASH,
            "user_skey": user_skey,
            "app_id": review_info.app_ids[0],
            "locale": _get_locale(),
            "distro": DISTRO,
            "version": version or "unknown",
            "user_display": user_display,
            "summary": summary,
            "description": description,
            "rating": int(rating) * 20,
        }
        thread = threading.Thread(target=self._submit_review_thread, args=(review_info, request, callback), daemon=True)
        thread.start()

    def _submit_review_thread(self, review_info, request, callback):
        success = False
        message = None
        try:
            r = requests.post("%s/submit" % ODRS_API_URL, json=request, timeout=15)
            try:
                result = r.json()
                success = bool(result.get("success", False))
                message = result.get("msg")
            except ValueError:
                message = r.reason
        except Exception as e:
            message = str(e)

        GLib.idle_add(callback, success, message)
