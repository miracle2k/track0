import pytest
from .helpers import rules, internet, arglogger, block
from tests.test_cli import testable_cli_rules
from track.cli import CLIRules, Script

# Import fixtures
from .helpers import spider, spiderfactory


class TestNormalizeUrl:

    def test_lossless(self, spider):
        # Trailing slash
        spider.add('http://elsdoerfer.name')
        link = spider._link_queue.pop()
        assert link.url == 'http://elsdoerfer.name/'
        assert link.parsed.path == '/'

        # Case-sensitive hostname, default port
        spider.add('HTTP://elsdoerFER.NaME:80')
        link = spider._link_queue.pop()
        assert link.url == 'http://elsdoerfer.name/'

        # Empty query string
        spider.add('http://elsdoerfer.name/?')
        link = spider._link_queue.pop()
        assert link.url == 'http://elsdoerfer.name/'

    def test_lossy(self, spider):
        """These are normalizations that must be restored when a
        url is outputted to the mirror.
        """
        # Fragments
        spider.add('HTTP://elsdoerfer.name/#foo')
        link = spider._link_queue.pop()
        assert link.url == 'http://elsdoerfer.name/'
        assert link.original_url == 'http://elsdoerfer.name/#foo'

        # https
        spider.add('https://elsdoerfer.name/')
        link = spider._link_queue.pop()
        assert link.url == 'http://elsdoerfer.name/'

    def test_lossy_normalizations_in_mirror(self, spider):
        with internet(**{
            'bar': dict(stream='<a href="/foo#some-fragment"></a>'),
            'foo': dict(),
        }) as uris:
            spider.add(uris[0])
            spider.loop()

            # the local link contains the fragment
            content = spider.mirror.get_file(uris[0])
            assert b'"./foo.htm#some-fragment"' in content


class TestDuplicateHandling:
    """This is a bit more tricky than you might think because while
    we want to minimize any duplicate processing, we might have to
    account for rules like "@save +depth-3 -tag=a" which mean that
    the same url might be skipped 100 times only to match the 101 time.
    """

    def test_filtered_before_queue(self, spider):
        """Test that duplicates are not even added to the queue
        to begin with."""
        urlspec = {'http://example.org': '<a href="http://example.org"><a>'}
        with internet(**urlspec) as uris:
            spider.add(uris[0])
            spider.process_one()

            # No link was added to the queue
            assert len(spider) == 0

    def test_only_saved_urls_can_be_duplicates(self, spider):
        """The same url can be queued multiple times until it
        is downloaded at least once. This is because whether
        a link is to be saved may depend on where it is found.
        """
        urlspec = {'http://example.org': '<a href="/link"><a>',
                   'http://example.org/link': ''}
        with internet(**urlspec) as uris:
            # Run through spider once, do not save /link
            spider.rules._save = lambda l: False
            spider.add(uris[0])
            spider.loop()

            # Try again with a different rule
            spider.rules._save = lambda l: True
            spider.add(uris[0])
            spider.process_one()

            # The link was added to the queue
            assert len(spider) == 1

    def test_duplicates_can_be_added_to_the_queue(self, spider):
        """Slightly different version of
        "test_only_saved_urls_can_be_duplicates".

        The same url will be added to the queue multiple times until
        it has been processed once.
        """
        urlspec = {'http://example.org/1': '<a href="/link"><a>',
                   'http://example.org/2': '<a href="/link"><a>',
                   'http://example.org/link': ''}
        with internet(**urlspec) as uris:
            # Process multiple pages linking to the same url
            spider.add('http://example.org/1')
            spider.add('http://example.org/2')
            spider.process_one()
            spider.process_one()

            # That url is now on the queue twice
            assert len(spider) == 2
            assert spider._link_queue[0].url == 'http://example.org/link'
            assert spider._link_queue[1].url == 'http://example.org/link'

    def test_duplicates_in_user_api(self, spider):
        """Duplicates and the public spider.add() method.
        """
        urlspec = {'http://example.org': '<a href="http://example.org"><a>'}
        with internet(**urlspec) as net:
            # Duplicates are allowed on the queue
            spider.add(net[0])
            spider.add(net[0])
            assert len(spider) == 2

            # But they are not processed twice
            spider.loop()
            assert len(list(net.requests.elements())) == 1

        with internet(**urlspec) as net:
            # Previously processed url can be added again, but is not processed.
            spider.add(net[0])
            assert len(spider) == 1
            spider.loop()
            assert len(list(net.requests.elements())) == 0


def test_error_code(spider):
    """Error pages are ignored; they are never saved nor followed.
    """
    with internet(foo=('<a href="http://google.de">', 404)) as uris:
        spider.add(uris[0])
        spider.process_one()

        # Nothing was saved
        assert len(spider.mirror.encountered_urls) == 0
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
        assert spider._link_queue[-1].url.endswith('/meta.rdf')
        assert spider._link_queue[-1].source == 'http-header'

        # The first url is a "requisite" link
        spider = spiderfactory()
        spider.add(uris[1])   # foo
        spider.process_one()
        assert len(spider) == 1
        assert spider._link_queue[-1].url.endswith('/style.css')
        assert spider._link_queue[-1].info['inline'] == True


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
            spider.rules._follow = lambda link: not 'bar' in link.url

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
            cli_rules = testable_cli_rules(follow=['-', '+size>1m'])
            spider = spiderfactory(rules=cli_rules)

            spider.add('http://example.org/foo', source=None)
            spider.loop()

            # The redirect was followed despite the redirecting url not
            # matching the size filter
            assert len(spider.mirror.encountered_urls) == 1

    def test_ident_redirect(self, spider):
        """A url redirecting to itself.
        """
        with internet(**{
            'http://example.org/foo': dict(
                    status=302,
                    headers={'Location': 'http://example.org/foo'}),
        }) as urls:
            spider.events.follow_state_changed = arglogger()
            spider.add(urls[0])
            spider.process_one()

            # Redirect is not added, this is an error
            assert len(spider) == 0
            assert spider.events.follow_state_changed.kwarg('failed')


class TestSkipDownload:
    """Test the Rules.skip_download API."""

    def test_manual_skip_rule(self, spiderfactory):
        """A custom rules implementation allows skipping downloads.
        """
        spider = spiderfactory()
        spider.rules.skip_download = lambda link, spider: True
        with internet() as net:
            # Currently download skipping raises an error if the
            # mirror does not know the url yet. While this makes
            # sense for the intended purpose of skip_download(),
            # possibly this should not be required from an API
            # design standpoint. Certainly, making this test work is ugly.
            spider.mirror.stored_urls['http://example.org/'] = ['']
            spider.mirror.url_info['http://example.org/'] = {'links': [], 'mimetype': False}

            spider.add('http://example.org/')
            spider.loop()

            # No requests have been executed
            assert net.requests == {}

            # But the url has been registered as seen
            assert 'http://example.org/' in spider.mirror.encountered_urls


    def test_expires(self, spider):
        """:class:`DefaultRules` implements an expires check.
        """
        urlspec = {
            'http://example.org/foo': dict(
                    headers={'Expires': 'Sun, 12 Oct 2914 01:51:19 GMT'}),
        }
        with internet(**urlspec) as net:
            # The first time, the expires header will be stored
            spider.add(net[0])
            spider.loop()

            assert net.requests[net[0]] == 1

        with internet(**urlspec) as net:
            # The next time, we simply do not download the file
            spider.add(net[0])
            spider.loop()

            assert net.requests[net[0]] == 0


class TestLocalFiles:

    def test_basic_dealing_with_localfile(self, spider, tmpdir):
        file = tmpdir.join('input.html')
        file.write('<a href="google"></a>')
        spider.add('{}{}'.format(file.strpath, '{http://example.org}'))
        spider.process_one()

        assert len(spider) == 1
        assert spider._link_queue[0].url == 'http://example.org/google'

    def test_css_localfile(self, spiderfactory, tmpdir):
        css = """background-image: url('test.gif');"""

        # Filename indicates the CSS nature
        with block(spiderfactory()) as spider:
            file = tmpdir.join('input.css')
            file.write(css)
            spider.add('{}{}'.format(file.strpath, '{http://example.org}'))
            spider.process_one()
            assert len(spider) == 1
            assert spider._link_queue[0].url == 'http://example.org/test.gif'

        # URL indicates the CSS nature
        with block(spiderfactory()) as spider:
            file = tmpdir.join('input')
            file.write(css)
            spider.add('{}{}'.format(file.strpath, '{http://example.org/input.css}'))
            spider.process_one()
            assert len(spider) == 1
            assert spider._link_queue[0].url == 'http://example.org/test.gif'

        # Nothing indicates the CSS nature
        with block(spiderfactory()) as spider:
            file = tmpdir.join('input')
            file.write(css)
            spider.add('{}{}'.format(file.strpath, '{http://example.org/}'))
            spider.process_one()
            # This was not parsed as CSS so no link was found
            assert len(spider) == 0

    def test_never_mirrored(self, tmpdir, spider):
        """Local file links are never added to the mirror."""
        file = tmpdir.join('input.html')
        file.write('<a href="google"></a>')
        spider.add('{}{}'.format(file.strpath, '{http://example.org}'))
        spider.process_one()

        assert len(spider.mirror.encountered_urls) == 0

