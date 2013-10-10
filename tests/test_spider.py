import pytest
from .helpers import TestableSpider, MemoryMirror, rules, internet, arglogger
from track.cli import CLIRules, Script


@pytest.fixture(scope='function')
def spider(**kwargs):
    defaults = dict(rules=rules(), mirror=MemoryMirror())
    defaults.update(kwargs)
    spider = TestableSpider(**defaults)
    return spider


@pytest.fixture(scope='session')
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


class TestRedirects:
    """Make sure redirects are handled correctly.

    This isn't all that straightforward.
    """

    def test_rules_are_applied(self, spider):
        """The redirect target must pass the follow rules, as well as
        the redirecting url, but not any urls in between.
        """
        with internet(**{
            'http://example.org/foo': dict(
                    status=302, headers={'Location': 'http://example.org/bar'}),
            'http://example.org/bar': dict(
                    status=302, headers={'Location': 'http://example.org/baz'}),
            'http://example.org/baz': dict(stream='ok'),
        }):
            spider.rules._follow = arglogger(return_value=True)

            spider.add('http://example.org/foo', source=None)
            spider.loop()

            calls = spider.rules._follow.arg(0)
            assert len(calls) == 2
            assert calls[0].url == 'http://example.org/foo'
            assert calls[1].url == 'http://example.org/baz'

    def test_redirects_in_mirror(self, spider):
        """Test that links to urls that redirect are properly resolved
        in the local copy.
        """
        with internet(**{
            'http://example.org/foo': dict(
                    status=302, headers={'Location': 'http://example.org/bar'}),
            'http://example.org/bar': dict(),
            'http://example.org/qux': dict(links=['foo']),
        }):
            spider.add('http://example.org/foo')
            spider.add('http://example.org/qux')
            spider.loop()

            # Despite linking to `foo`, the local copy links to `bar`,
            # the redirect target of `foo`.
            content = spider.mirror.get_file('http://example.org/qux')
            assert b'"./bar.htm"' in content

    @pytest.mark.parametrize(('redir_code',), ((301,), (302,)))
    def test_external_redirects_in_mirror(self, spider, redir_code):
        """We try to have some extra smarts about links that go to
        pages that are not included in the mirror, but of which we
        know that they are redirects.
        """
        with internet(**{
            'http://example.org/foo': dict(
                    status=redir_code,
                    headers={'Location': 'http://example.org/bar'}),
            'http://example.org/bar': dict(),
            'http://example.org/qux': dict(links=['foo']),
        }):
            # Setup save rule such that the redirect target, bar,
            # is not followed and thus not added to the mirror.
            spider.rules._follow = lambda url: not 'bar' in url.url

            spider.add('http://example.org/foo', source=None)
            spider.add('http://example.org/qux')
            spider.loop()

            # If it's a permanent redirect, it is rewritten to the target.
            # Otherwise, we keep the original link.
            content = spider.mirror.get_file('http://example.org/qux')
            if redir_code == 301:
                assert b'"http://example.org/bar"' in content
            else:
                assert b'"http://example.org/foo"' in content

    def test_filters(self, spiderfactory):
        """Test how certain filters behave with redirects.
        """
        with internet(**{
            'http://example.org/foo': dict(
                    status=302,
                    headers={'Location': 'http://example.org/bar'}),
            'http://example.org/bar': dict(headers={'content-length': 5*1024*1024*1024})
        }):
            args = Script.get_default_namesspace()
            args.follow = ['-', '+size>1m']

            # Make a CLIRules instance that uses the test internet
            cli_rules = CLIRules(args)
            cli_rules.configure_session = lambda s: rules.configure_session(cli_rules, s)

            spider = spiderfactory(rules=cli_rules)

            spider.add('http://example.org/foo', source=None)
            spider.loop()

            # The redirect was followed despite the redirecting url not
            # matching the size filter
            assert len(spider.mirror.urls) == 1



