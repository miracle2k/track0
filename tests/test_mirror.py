import pytest
from tests.helpers import fake_response
from track.mirror import Mirror
from track.spider import Link


@pytest.fixture(scope='function')
def mirror(tmpdir):
    mirror = Mirror(tmpdir.strpath)
    mirror.write_at_once = False
    mirror.backups = False
    return mirror


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


