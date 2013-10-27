from http.cookiejar import CookieJar, CookiePolicy
import shelve


__all__ = ('ShelvedCookieJar', 'RefuseAll', 'NoneDict')


class ShelvedCookieJar(CookieJar):
    """A cookie jar backed by a shelf.

    This means that different from any of the builtin FileCookieJar
    implementations, you do not need to call save(), and it will not
    rewrite the full cookie jar in order to persist a new one.
    """

    def __init__(self, filename, **kwargs):
        super().__init__(**kwargs)
        if isinstance(filename, shelve.Shelf):
            self.shelf = filename
        else:
            self.shelf = shelve.open(filename)

    def _get_cookies(self):
        return self.shelf
    def _set_cookies(self, value):
        if not hasattr(self, 'shelf'):
            # This is during __init__
            return
        self.shelf.clear()
        self.shelf.update(value)
    _cookies = property(_get_cookies, _set_cookies)

    def set_cookie(self, cookie):
        self._cookies_lock.acquire()
        try:
            # We need to do this ourselves to make it persist
            d = self._cookies.setdefault(cookie.domain, {})
            d.setdefault(cookie.path, {})[cookie.name] = cookie
            self._cookies[cookie.domain] = d
        finally:
            self._cookies_lock.release()

    def clear(self, domain=None, path=None, name=None):
        super().clear(domain, path, name)
        if (not path is None) or (not name is None):
            # This matches the cases where we need to manually
            # make sure that that the key is marked dirty.
            self._cookies[domain] = self._cookies[domain]


class RefuseAll(CookiePolicy):
    def set_ok(self, cookie, request):
        return False


class NoneDict(dict):
    def __getitem__(self, key):
        return dict.get(self, key)
