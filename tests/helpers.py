from contextlib import contextmanager
from io import StringIO, BytesIO
import requests.adapters
from requests_testadapter import TestSession, Resp
from track.spider import Spider as BaseSpider, Rules as BaseRules
from track.mirror import Mirror as BaseMirror


class TestAdapter(requests.adapters.HTTPAdapter):
    """The TestAdapter that comes with the ``requests_testadapter``
    module isn't right for our purposes. Maybe we can get rid of the
    module all together.

    This adapter has a class-based (i.e. global) list of urls that
    it responds to. This allows a test to setup it's routes
    independently of it's spider instance (and therefore requests
    session).
    """

    urls = {}

    def send(self, request, stream=False, timeout=None,
             verify=True, cert=None, proxies=None):
        if not request.url in self.urls:
            raise ConnectionError('no such virtual url', request.url)
        resp = Resp(**self.urls[request.url])
        r = self.build_response(request, resp)
        if not stream:
            # force prefetching content unless streaming in use
            r.content
        return r


class MemoryMirror(BaseMirror):
    """Mirror that does not write to the file system.
    """

    def __init__(self, **kwargs):
        BaseMirror.__init__(self, '/tmp', **kwargs)

    def open(self, filename, mode):
        if 'b' in mode:
            return BytesIO()
        return StringIO()


class TestableSpider(BaseSpider):

    # Session that has no adapters
    session_class = TestSession


class rules(BaseRules):
    """Easy rules setup for tests.
    """

    def __init__(self, follow=True, save=True, stop=False):
        self._follow = follow
        self._save = save
        self._stop = stop

    def save(self, url):
        if callable(self._save):
            return self._save(url)
        return bool(self._save)

    def follow(self, url):
        if callable(self._follow):
            return self._follow(url)
        return bool(self._follow)

    def stop(self, url):
        if callable(self._stop):
            return self._stop(url)
        return bool(self._stop)

    def configure_session(self, session):
        session.mount('http://', TestAdapter())
        session.mount('https://', TestAdapter())


@contextmanager
def internet(**urls):
    """Setup fake urls for :class:`TestAdapter`.

    ::

        with internet(**{'http://example.org/foo': {'stream': 'out', code=200}}):
            ...

    ::

        with internet(foo='out'):
            ...

    The examples are equivalent.

    Returns the actual urls that can be requested. Since there is
    unfortunately no way to guarantee the order in which the kwargs
    were specified, they are instead returned in sorted order.
    """
    old_urls = TestAdapter.urls
    try:
        final_urls = {}
        for url, data in urls.items():
            # Support shortcut urls
            if not ':' in url:
                url = 'http://example.org/%s' % url
            # Support shortcut responses
            if not isinstance(data, dict):
                if isinstance(data, tuple):
                    # 2-tuple
                    content, code = data
                else:
                    # Simple string
                    content = data
                    code = 200

                data = {
                    'stream': content,
                    'status': code
                }

            # Final fixes to the data
            data.setdefault('stream', '')
            if isinstance(data['stream'], str):
                data['stream'] = data['stream'].encode('utf-8')

            final_urls[url] = data

        TestAdapter.urls = final_urls
        yield list(sorted(final_urls.keys()))
    finally:
        TestAdapter.urls = old_urls
