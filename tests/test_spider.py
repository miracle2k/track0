from contextlib import contextmanager

import pytest
from .helpers import TestableSpider, MemoryMirror, rules, internet


@pytest.fixture
def spider():
    spider = TestableSpider(rules(), MemoryMirror())
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
    with internet(foo=('<a href="http://google.de">', 404)) as uris:
        spider.add(uris[0])
        spider.process_one()

        # Nothing was saved
        assert len(spider.mirror.urls) == 0
        # No further links were added to the queue
        assert len(spider) == 0
