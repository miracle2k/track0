from contextlib import contextmanager

import pytest
from .helpers import TestableSpider, MemoryMirror, rules, internet


@pytest.fixture
def spider():
    spider = TestableSpider(rules(), MemoryMirror())
    return spider


@pytest.fixture
def spiderfactory():
    return spider


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
    """Error pages are ignored; they are never saved nor followed.
    """
    with internet(foo=('<a href="http://google.de">', 404)) as uris:
        spider.add(uris[0])
        spider.process_one()

        # Nothing was saved
        assert len(spider.mirror.urls) == 0
        # No further links were added to the queue
        assert len(spider) == 0


def test_http_header_links(spiderfactory):
    """The HTTP headers may include links.
    """
    # Maybe useful in the future here: requests.utils.parse_header_links

    with internet(
            bar=dict(headers={
                'Link': '<meta.rdf>; rel=meta'
            }),
            foo=dict(headers={
                'Link': '<style.css>; rel=stylesheet'
            })) as uris:

        # The first url adds a "standard" link
        spider = spiderfactory()
        spider.add(uris[0])   # bar
        spider.process_one()
        assert len(spider) == 1
        assert spider._url_queue[-1].url.endswith('/meta.rdf')
        assert spider._url_queue[-1].source == 'http-header'

        # The first url is a "requisite" link
        spider = spiderfactory()
        spider.add(uris[1])   # foo
        spider.process_one()
        assert len(spider) == 1
        assert spider._url_queue[-1].url.endswith('/style.css')
        assert spider._url_queue[-1].requisite == True
