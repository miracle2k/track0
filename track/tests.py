from genericpath import commonprefix
from os.path import basename, splitext
from track.cli import Redirect
from track.spider import get_content_type


class TestImpl(object):

    @staticmethod
    def default(link):
        """Special case for +/- defaults without test name.
        """
        return True

    @staticmethod
    def requisite(link, ctx):
        """Passes if the url is necessary to display a page that has
        been saved. This includes images, stylesheets script files, but
        also things that are more rare, like iframes or embeds.
        """
        if not link.info.get('inline', False):
            return False

        if not link.previous:
            return False

        # The link that was inlining this must have been saved.
        #
        # You might question whether it is correct to intermix the concepts
        # of "url" and "link" here. I believe the answer is yes, because for
        # each unique url only one link will ever be processed anyway. I.e.
        # there is no way that previous.url will ever NOT be the one that
        # was saved to the mirror. So there is no way a different
        # previous.url could con us into accepting a requirement.
        if not link.previous.url in ctx['spider'].mirror.encountered_urls:
            return False

        return True

    @staticmethod
    def robots(link, ctx):
        """Passes if the url is disallowed by robots instructions.
        """
        return not ctx['spider'].robots.allowed(link.url)

    @staticmethod
    def depth(link):
        """Tests the depth of the link within the discovery process. A
        starting link has a depth of 0, a link found within that
        starting link has a depth of 1, links found on that second page
        have a depth of 2.

        Note that you need to test using comparison operators. To go
        four levels deep, you would use::

            @follow +depth<=4

        But if we said instead::

            @follow +depth=4

        the rule would only match pages that have been discovered after
        following three previous links, and without other rules you would
        never get that far.
        """
        return link.depth

    @staticmethod
    def domain_depth(link):
        """This is like "depth" except that the counter resets after
        the domain changes while spidering. For example::

            track URL @follow +domain-depth=0

        Will download the first page of every external link on the page,
        but will not follow any internal links (where the depth would be
        1 for the first link found on a starting link).
        """
        return link.domain_depth

    @staticmethod
    def original_domain(link):
        """Passes urls that are on the same domain as the root url which
        was the starting point for the discovery of this url.

        The check runs before any duplicates are filtered out. This means
        that if there are two starting urls, ``a.com`` and ``b.com``, and
        ``a.com`` discovers ``b.com/foo`` before ``b.com`` itself does,
        the url *will* be followed the second time around.
        """
        return link.parsed.netloc == link.root.parsed.netloc

    @staticmethod
    def same_domain(link):
        """Passes urls that are on the same domain as the previous url
        where they were found.

        This is not the same as ``original-domain``. For, example,
        consider this rule::

            +same-domain +tag="link"

        This will cause the spider to at first remain on the starting
        domain, like ``original-domain`` would as well. But we also
        follow urls that we have discovered through <link> tags, and
        once a <link> tag leads to a different domain, further links
        on that domain are followed as well.
        """
        if not link.previous:
            return True
        return link.parsed.netloc == link.previous.parsed.netloc

    @staticmethod
    def down(link):
        """Passes urls that are further down the path hierarchy than
        the starting point. For example, given this command::

            $ track http://www.example.org/foo/bar @follow +down

        then ``http://www.example.org/foo/baz`` would pass, but
        ``http://www.example.org/qux`` would not, and neither would
        ``http://google.com``. That is, this is a more restrictive
        version of ``original-domain``.

        .. note::
            This is a handy shortcut for ``path-distance-to-original>0``.
            The ``path-distance-to-original`` gives you even more
            control, like controlling how deep to go. It even allows
            going upwards.
        """
        return TestImpl.path_distance_to_original(link) >= 0

    @staticmethod
    def path_level(link):
        """Test the depth of the path of an url.

        The path level of ``http:/example.org/`` is 0, the path level of
        ``http:/example.org/foo/`` is 1, and the path level of
        ``http:/example.org/foo/bar/`` is 2. However, the level of
        ``http:/example.org/foo/bar`` or ``http:/example.org/foo/bar.html``
        (i.e. no trailing slash) is 1.

        This is not to be confused with the "depth" test which checks the
        depth of the spidering process.
        """
        return len(link.parsed.path.split('/')) - 2

    @staticmethod
    def path_distance(link):
        """The path distance is the difference in the values as returned
        by ``path-level`` between the url, and the previous one::

            @follow +path-distance=1

        This means that going from ``http:/example.org/foo/`` to
        ``http:/example.org/foo/bar/`` is allowed, but going to
        ``http:/example.org/foo/index.html`` is not.

        The distance can be positive or negative.

        There is no distance between ``/foo/`` and ``/bar/`, nor is there
        a distance between two urls on different domains. The test will
        never pass in such cases.
        """
        # Short-circuit root links
        if link.previous is None:
            return 0
        return TestImpl._path_distance(link, link.previous)

    @staticmethod
    def path_distance_to_original(link):
        """Like ``path-distance``, but tests the difference between the
        url and the original root url that was the starting point.

        A common use case is only following urls that are further down
        the hierarchy, which can be accomplished using::

            @follow +path-distance-to-original>=0

        Because it is so common, this test has a simple version available:

            @follow +down
        """
        # Short-circuit root links
        if link.previous is None:
            return 0
        return TestImpl._path_distance(link, link.root)

    @staticmethod
    def _path_distance(link1, link2):
        # Test never passes if the domains have changed
        if link1.parsed.netloc != link2.parsed.netloc:
            return False

        source = link2.parsed.path.split('/')
        this = link1.parsed.path.split('/')
        shared = commonprefix([source, this])

        # /foo and /bar also will never pass
        if len(shared) < len(source) and len(shared) < len(this):
            return False

        return len(this) - len(source)

    @staticmethod
    def url(link):
        """Match against the full url, including query string.
        """
        return link.url

    @staticmethod
    def protocol(link):
        """Match against the protocol of the url.

        This will be something like ``http`` or ``https``.
        """
        return link.parsed.scheme

    @staticmethod
    def domain(link):
        """Match against the domain part of the url.

        For example, if the url is ``http://www.apple.com/iphone/``,
        then the domain will be ``http://www.apple.com``.
        """
        return link.parsed.netloc

    @staticmethod
    def port(link):
        """Match against the port of the url.

        For example, if the url is ``http://example.org:8080``, the port
        is ``8080``. You can run numeric comparisons again it (larger than,
        smaller than etc).

        If the url does not specify a port, ``80`` is used.
        """
        return link.parsed.port or 80

    @staticmethod
    def path(link):
        """Match against the path part of the url.

        For example, if the url is ``http://www.apple.com/iphone/``,
        then the path will be ``/iphone/``. At a minimum, the path
        will always be a single slash ``/``.
        """
        # "http://example.org" would return an empty string, do not
        # let that happen.
        return link.parsed.path or '/'

    @staticmethod
    def filename(link):
        """Match against the filename of a url.

        For example, if the url is ``http://example.org/foo/index.html``,
        the filename will be ``index.html``.

        If the url is ``http://example.org/foo/``, the filename will be
        empty. If the url is ``http://example.org/foo`` the filename will
        be ``foo``.
        """
        return basename(link.parsed.path)

    @staticmethod
    def extension(link):
        """Match against the file extension.

        For example, if the url is ``http://example.org/foo/index.html``,
        the extension will be ``html``.

        If there is no file extension, this test will match an empty string.
        """
        return splitext(basename(link.parsed.path))[1][1:]

    @staticmethod
    def querystring(link):
        """Match against the query string.

        For example, if the url is ``http://example.org/foo/?page=2&user=1``,
        the querystring will be ``page=2&user=1``.
        """
        return link.parsed.query

    @staticmethod
    def fragment(link):
        """Match against the link fragment.

        For example, if a link on the page is
        ``http://example.org/foo/#introduction``, the fragment will be
        ``introduction``.
        """
        return link.parsed.fragment

    @staticmethod
    def size(link, ctx):
        """Test the size of the document behind a url.

        You may use K, M or G as units::

            +size<1M

        Note: This will execute a HEAD request to the url to determine
        the size. If the HEAD request does not include information about
        the size, the full url needs to be fetched.
        """
        response = link.resolve(ctx['spider'], 'head')
        if not response:
            return None
        if response.redirects:
            raise Redirect()
        length = response.headers.get('content-length', None)
        if length is None:
            response = link.resolve('full')
            if not response:
                return None
            length = response.headers.get('content-length', None)
            if not length:
                # Force downloading the content
                length = len(response.content)
        return length


    @staticmethod
    def content_type(link, ctx):
        """Match against the content type of the url.

        A content type might be ``text/html`` or ``image/png``.

        Note: This will execute a HEAD request to the url to determine
        the content type.
        """
        response = link.resolve(ctx['spider'], 'head')
        if not response:
            return None
        return get_content_type(response)

    @staticmethod
    def content(link, ctx):
        """Match against the content of the url.

        Careful! This test requires a url to be downloaded in full .
        """
        response = link.resolve(ctx['spider'], 'full')
        if not response:
            return None
        return response.text

    @staticmethod
    def tag(link):
        """The tag and attribute where the url was found.

        For example, if the spider followed a standard link, this would
        return ``a.href``. Other possible values include, for example,
        ``img.src`` or ``script.src``.

        If the link was not found in a tag, this matches an empty string.
        """
        return link.extra.get('tag', '')


AvailableTests = {
    '': TestImpl.default,

    # Operating on the spidering process
    'depth': TestImpl,
    'domain-depth': TestImpl,

    # Operating on the relationship between urls
    'original-domain': TestImpl,
    'same-domain': TestImpl,
    'down': TestImpl,
    'path-level': TestImpl,
    'path-distance': TestImpl,
    'path-distance-to-original': TestImpl,

    # Operating on the URL itself
    'url': TestImpl,
    'protocol': TestImpl,
    'domain': TestImpl,
    'port': TestImpl,
    'path': TestImpl,
    'filename': TestImpl,
    'extension': TestImpl,
    'querystring': TestImpl,
    'fragment': TestImpl,

    # Operating on URL metadata (headers)
    'content-type': TestImpl,
    'size': TestImpl,
    'content': TestImpl,

    # Operating on the url/discovery source
    'tag': TestImpl,
    'requisite': TestImpl,
    'robots': TestImpl,
}
