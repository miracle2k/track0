from track.parser import CSSParser, HTMLParser


class TestCSSParser(object):

    def test(self):
        css = CSSParser("""
        @import 'double " in single';
        @import "single ' in double";
        @import "escaped \\" double";
        @import url(import with url);
        url(url with no quotes)
        url('url with single quotes')
        url("url with double quotes")
        """, '')

        css = list(css)

        assert ['double " in single',
                "single ' in double",
                'escaped " double',
                'import with url',
                'url with no quotes',
                'url with single quotes',
                'url with double quotes'] == [url for url, _ in css]



class TestHTMLParser(object):

    def urls(self, html):
        return [url for url, opts in HTMLParser(html, 'http://example.org')]

    def urls_with_opts(self, html):
        result = HTMLParser(html, 'http://example.org')
        return [r[0] for r in result], [r[1] for r in result]

    def test_external_stylesheet(self):
        urls, opts = self.urls_with_opts(b"""
            <link href="home.css" rel="stylesheet" />""")
        assert urls[0] == 'http://example.org/home.css'
        assert opts[0].get('inline') is True

    def test_alternate_stylesheet(self):
        urls, opts = self.urls_with_opts(b"""
            <link href="home.css" rel="alternate stylesheet" />""")
        assert urls[0] == 'http://example.org/home.css'
        assert opts[0].get('inline') is True

    def test_base(self):
        assert self.urls(b"""
        <base href="/bar/">
        <a href="foo">""") == ['http://example.org/bar/foo']

    def test_attrs_with_whitespace(self):
        assert self.urls(b"""<a href="
        /foo">""") == ['http://example.org/foo']



