from contextlib import contextmanager
from io import StringIO, BytesIO
import httpretty
import pytest
from track.spider import Spider, Rules as BaseRules
from track.mirror import Mirror as BaseMirror


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


class FakeMirror(BaseMirror):
    def __init__(self, **kwargs):
        BaseMirror.__init__(self, '/tmp', **kwargs)
    def open(self, filename, mode):
        if 'b' in mode:
            return BytesIO()
        return StringIO()


def register_uri(uri, body, method=httpretty.GET, **kwargs):
    kw = dict(content_type='text/html')
    kw.update(kwargs)
    httpretty.register_uri(
        method, uri, body=body, **kw)


@pytest.fixture
def spider():
    spider = Spider(rules(), FakeMirror())
    return spider


@contextmanager
def serve(body, **kw):
    httpretty.reset()
    httpretty.enable()
    try:
        uri = 'http://example.org/'
        register_uri(uri, body, **kw)
        yield uri
    finally:
        httpretty.disable()


def test_normalize_url(spider):
    # Trailing slash
    spider.add('http://elsdoerfer.name')
    url = spider._url_queue.pop()
    assert url.url == 'http://elsdoerfer.name/'
    assert url.parsed.path == '/'

    # Case-sensitive hostname, default port
    spider.add('HTTP://elsdoerFER.NaME:80')
    url = spider._url_queue.pop()
    assert url.url == 'http://elsdoerfer.name/'


def test_error_code(spider):
    with serve('<a href="/foo">', status=404) as uri:
        spider.add(uri)
        spider.process_one()

        # Nothing was saved
        assert len(spider.mirror.urls) == 0
        # No further links were added to the queue
        assert len(spider) == 0
