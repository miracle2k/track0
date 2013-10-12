from contextlib import contextmanager
from io import StringIO, BytesIO, TextIOWrapper
import pytest
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


class FakeFile(BytesIO):
    """A virtual file backed by a dictionary of filenames. The given
    dict and key represent the data storage area where changes to the
    file are written to.

    Rather than implementing the file interface fully, we just use a
    BytesIO and copy the data on flush.

    In principal, the challenge with simply using a standard BytesIO
    instance to fake a file is that after close() is called, the data
    is no longer accessible.

    See also pyfakefs, which I judged to large for use here.
    """
    def __init__(self, dict, key):
        self.dict, self.key = dict, key
        BytesIO.__init__(self, dict[key])

    def close(self):
        self.flush()
        BytesIO.close(self)

    def flush(self):
        self.dict[self.key] = self.getvalue()
        BytesIO.flush(self)


class MemoryMirror(BaseMirror):
    """Mirror that does not write to the file system.
    """

    def __init__(self, **kwargs):
        BaseMirror.__init__(self, '/tmp', **kwargs)
        self.virtual_files = {}

    def open(self, filename, mode):
        self.virtual_files.setdefault(filename, b'')
        buffer = FakeFile(self.virtual_files, filename)

        if 'b' in mode:
            return buffer
        return TextIOWrapper(buffer)

    def open_shelve(self, filename):
        return {}

    def flush(self):
        pass

    def get_file(self, url):
        """Return the virtual file for this url.
        """
        filename = self.encountered_urls[url]
        return self.virtual_files[filename]


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

    def save(self, link, spider):
        if callable(self._save):
            return self._save(link)
        return bool(self._save)

    def follow(self, link, spider):
        if callable(self._follow):
            return self._follow(link)
        return bool(self._follow)

    def stop(self, link, spider):
        if callable(self._stop):
            return self._stop(link)
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

    def make_url(url):
        # Support shortcut urls
        if not ':' in url:
            url = 'http://example.org/%s' % url
        return url

    old_urls = TestAdapter.urls
    try:
        final_urls = {}
        for url, data in urls.items():
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

            # Generate the document content.
            data.setdefault('stream', b'')
            # Make sure we are dealing with bytes
            if isinstance(data['stream'], str):
                data['stream'] = data['stream'].encode('utf-8')

            # Auto-generating links
            links = data.pop('links', False)
            if links:
                link_html = []
                for link in links:
                    link_html.append(
                        '<a href="{}">{}</a>'.format(make_url(link), link))
                data['stream'] += "".join(link_html).encode('utf-8')

            # Setup some default headers
            data.setdefault('headers', {})
            data['headers'].setdefault('content-length', len(data['stream']))
            data['headers'].setdefault('content-type', 'text/html')

            final_urls[make_url(url)] = data

        TestAdapter.urls = final_urls
        yield list(sorted(final_urls.keys()))
    finally:
        TestAdapter.urls = old_urls


class arglogger:
    """A helper callable that will log each call made.
    """
    def __init__(self, return_value=None):
        self.calls = []
        self.return_value = return_value
    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.return_value
    def arg(self, idx):
        return [args[idx] for args, kwargs in self.calls]


@pytest.fixture(scope='function')
def spider(**kwargs):
    defaults = dict(rules=rules(), mirror=MemoryMirror())
    defaults.update(kwargs)
    spider = TestableSpider(**defaults)
    return spider


@pytest.fixture(scope='session')
def spiderfactory():
    return spider
