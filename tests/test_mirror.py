import pytest
from requests_testadapter import TestSession
from tests.helpers import internet, TestAdapter
from track.mirror import Mirror
from track.parser import HeaderLinkParser, get_parser_for_mimetype
from track.spider import Link, get_content_type


@pytest.fixture(scope='function')
def mirror(tmpdir):
    mirror = Mirror(tmpdir.strpath)
    mirror.write_at_once = False
    mirror.backups = False
    return mirror


def fake_response(link, content, **response_data):
    """A fake response that can be added to the mirror.
    """
    # Use the fake internet system to generate a response object.
    # This is more reliable than putting on together manually.
    data = {'stream': content}
    data.update(response_data)
    with internet(**{link.original_url: data}):
        session = TestSession()
        session.mount('http://', TestAdapter())
        session.mount('https://', TestAdapter())
        response = session.request('GET', link.original_url)

    # Additional attributes are expected. This is what the spider
    # does before passing a link to mirror.add(). Possibly we should
    # have less code duplication here with the actual spider code.
    parser_class = get_parser_for_mimetype(get_content_type(response))
    if parser_class:
        response.parsed = parser_class(
            response.content, response.url, encoding=response.encoding)
    else:
        response.parsed = None
    response.links_parsed = HeaderLinkParser(response)
    return response


def get_mirror_file(mirror, url):
    with mirror.open(list(mirror.stored_urls[url])[0], 'r') as f:
        return f.read()


class TestSelectFilename:

    def get(self, mirror, url, **response_data):
        link = Link(url)
        response = fake_response(link, "", **response_data)
        return mirror.get_filename(link, response)

    def test_extension(self, mirror):
        assert self.get(mirror, 'http://example.org/foo') == 'example.org/foo.html'
        assert self.get(
            mirror, 'http://example.org/foo.php',
            headers={'content-type': 'text/html'}) == 'example.org/foo.html'
        assert self.get(
            mirror, 'http://example.org/foo.html',
            headers={'content-type': 'text/html'}) == 'example.org/foo.html'


class TestConvertLinks:

    def test_external_links_absolutized(self, mirror):
        """If a link points to a page not in the mirror,  it is replaced
        with an absolute link.
        """
        mirror.convert_links = True

        link = Link('https://example.org')
        response = fake_response(link, """
        <a href="/PATH#FOO">
        <a href="http://EXAMPLE.ORG/PATH#FOO">
        """)
        mirror.add(link, response)

        # NB: The correct protocol is used
        mirror._convert_links()
        assert get_mirror_file(mirror, link.url) == """
        <a href="https://example.org/PATH#FOO">
        <a href="http://EXAMPLE.ORG/PATH#FOO">
        """

    def test_links_that_contain_fragments(self, mirror):
        """[regression] Links that contain fragments are converted,
        and the fragment is maintained.
        """
        mirror.convert_links = True

        link = Link('http://example.org')
        response = fake_response(link, """
        <a href="https://EXAMPLE.ORG/#FOO">
        """)
        mirror.add(link, response)

        # Link conversion keeps #FOO around
        mirror._convert_links()
        assert get_mirror_file(mirror, link.url) == """
        <a href="./index.html#FOO">
        """


