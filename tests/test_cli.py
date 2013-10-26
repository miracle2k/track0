from tests.helpers import internet
from track.cli import OperatorImpl, CLIRules, Script
from track.spider import Link

# Import fixtures
from .helpers import spider, spiderfactory
from track.tests import TestImpl


def testable_cli_rules(**args):
    """Return a CLIRules instance that uses our test internet."""
    from .helpers import rules
    cli_rules = CLIRules(Script.get_default_namesspace(**args))
    cli_rules.configure_session = lambda s, p: rules.configure_session(cli_rules, s, p)
    return cli_rules


class TestRules(object):

    def test_down(self):
        test = lambda a,b: TestImpl.down(Link(a, Link(b)))

        assert not test('http://example.org/bar', 'http://example.org/foo/')
        assert not test('http://example.org/bar', 'http://example.org/foo')
        assert test('http://example.org/foo/bar', 'http://example.org/foo')

        # [regression]
        assert not test('http://example.org/foo/bar', 'http://example.de/foo')


    def test_path_level(self):
        test = lambda a: TestImpl.path_level(Link(a))

        assert test('http://example.org/') == 0
        assert test('http://example.org/foo') == 0
        assert test('http://example.org/foo/') == 1

    def test_path_distance(self):
        test = lambda a,b: TestImpl.path_distance(
            Link('http://example.org%s' % a, Link('http://example.org%s' % b)))

        assert test('/foo', '') == False
        assert test('/foo', '/bar') == False
        assert test('/foo', '/foobar') == False
        assert test('/foo', '/foo') == 0
        assert test('/foo', '/foo/bar') == -1
        assert test('/foo/', '/foo/bar') == 0
        assert test('/foo/bar', '/foo') == 1
        assert test('/foo/bar', '/foo/') == 0

    def test_protocol(self):
        test = lambda a: TestImpl.protocol(Link(a))

        assert test('http://www.example.org/') == 'http'

    def test_port(self):
        test = lambda a: TestImpl.port(Link(a))

        assert test('http://www.example.org/') == 80
        assert test('http://www.example.org:8080') == 8080

    def test_path(self):
        test = lambda a: TestImpl.path(Link(a))

        assert test('http://www.example.org/path/') == '/path/'
        assert test('http://www.example.org/') == '/'
        assert test('http://www.example.org') == '/'

    def test_filename(self):
        test = lambda a: TestImpl.filename(Link(a))

        assert test('http://www.example.org/path/') == ''
        assert test('http://www.example.org/path') == 'path'
        assert test('http://www.example.org/path/index.html') == 'index.html'

    def test_extension(self):
        test = lambda a: TestImpl.extension(Link(a))

        assert test('http://www.example.org/path/') == ''
        assert test('http://www.example.org/path') == ''
        assert test('http://www.example.org/index.html') == 'html'

    def test_querystring(self):
        test = lambda a: TestImpl.querystring(Link(a))

        assert test('http://www.example.org/path/?a=1&b=2') == 'a=1&b=2'
        assert test('http://www.example.org/path/') == ''

    def test_fragment(self):
        test = lambda a: TestImpl.fragment(Link(a))

        assert test('http://www.example.org/path/#fragment') == 'fragment'


def test_requisite_test(spiderfactory):
    """The requisite test is special in that it interacts with the
    mirror itself to know which urls have been saved.
    """
    with internet(**{
            'bar': dict(stream='<link rel="stylesheet" href="/foo" />'),
            'foo': dict(),
        }) as uris:
            # Under normal circumstances the requisite test will
            # pull in the stylesheet
            spider = spiderfactory()
            spider.rules = testable_cli_rules(follow=['+requisite'])
            spider.add(uris[0])
            spider.loop()
            assert len(spider.mirror.stored_urls) == 2

            # However, if we refuse the save the document, the requisite
            # doesn't count either.
            spider = spiderfactory()
            spider.rules = testable_cli_rules(
                follow=['+requisite'],
                save=['-path=/foo'])
            spider.add(uris[0])
            spider.loop()
            assert len(spider.mirror.stored_urls) == 0


class TestOperators(object):

    def test_numeric(self):
        assert OperatorImpl.truth(True) is True
        assert OperatorImpl.truth(False) is False
        assert OperatorImpl.truth(1) is True
        assert OperatorImpl.truth('') is False

        assert OperatorImpl.equality(1, '1') is True
        assert OperatorImpl.equality(1, '2') is False
        assert OperatorImpl.equality(1, '') is False
        assert OperatorImpl.equality(0, '') is False
        assert OperatorImpl.equality(1, 'abc') is False
        assert OperatorImpl.equality(False, '') is True

        assert OperatorImpl.inequality(1, '1') is False
        assert OperatorImpl.inequality(False, '') is False

        assert OperatorImpl.larger(4, '') is False

        assert OperatorImpl.smaller(800, '1K') is True
        assert OperatorImpl.smaller(1200, '1K') is False
        assert OperatorImpl.smaller(800, '1k') is True

    def test_string(self):
        assert OperatorImpl.equality('foo', 'foo') is True
        assert OperatorImpl.equality('foo', '*o') is True
