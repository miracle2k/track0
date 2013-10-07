from track.spider import Spider


def test_normalize_url():
    spider = Spider(None, None)

    # Trailing slash
    spider.add('http://elsdoerfer.name')
    url = spider._url_queue.pop()
    assert url.url == 'http://elsdoerfer.name/'
    assert url.parsed.path == '/'

    # Case-sensitive hostname, default port
    spider.add('HTTP://elsdoerFER.NaME:80')
    url = spider._url_queue.pop()
    assert url.url == 'http://elsdoerfer.name/'

