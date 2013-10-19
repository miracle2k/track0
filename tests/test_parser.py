# coding: utf-8
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

    def _make_parser(self, html, url='http://example.org'):
        parser = HTMLParser(html, url)
        # Always make sure the file can be correctly put together again
        assert parser.replace_urls(lambda s: None) == html
        return parser

    def parse(self, html, only_type=None, ignore=['unknown']):
        if not isinstance(only_type, (list, tuple)):
            only_type = [only_type]
        return [token
                for token in self._make_parser(html)._parse()
                if (only_type is not None and token['type'] in only_type)
                   or not token['type'] in ignore]

    def urls(self, html):
        return [url for url, opts in self._make_parser(html)]

    def urls_with_opts(self, html):
        parser = self._make_parser(html)
        return [r[0] for r in parser], [r[1] for r in parser]

    def replace(self, html, replacer):
        return self._make_parser(html).replace_urls(replacer)

    def test_entities(self):
        urls, opts = self.urls_with_opts(b"""
            <a href="f&quot;oo">""")
        assert urls[0] == 'http://example.org/f"oo'

    def test_form_action(self):
        urls, opts = self.urls_with_opts(b"""
            <form action="foo">""")
        assert urls[0] == 'http://example.org/foo'
        assert not opts[0].get('inline')
        assert opts[0].get('do-not-follow') is True

    def test_meta_refresh(self):
        urls, opts = self.urls_with_opts(
            b"""<meta http-equiv="refresh" content="10; url=index.html">""")
        assert urls[0] == 'http://example.org/index.html'
        assert not opts[0].get('inline')

        assert self.replace(
            b"""<meta http-equiv="refresh" content="10; url=index.html">""",
            lambda s: 'foo.html') ==\
                b"""<meta http-equiv="refresh" content="10; url=index.html">"""

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

    def test_link_tag_rel(self):
        urls, opts = self.urls_with_opts(b"""
            <link href="home.png" rel="icon" />
            <link href="home.png" rel="alternative icon" />
            <link href="home.png" rel="something else" />
            <link href="home.png" rel="apple-touch-icon" />
        """)
        assert urls[0] == 'http://example.org/home.png'
        assert opts[0].get('inline') is True
        assert opts[1].get('inline') is True
        assert opts[2].get('inline') is False
        assert opts[3].get('inline') is True

    def test_link_tag_without_rel(self):
        """[regression]"""
        urls, opts = self.urls_with_opts(b"""
            <link href="home.css" />""")
        assert urls[0] == 'http://example.org/home.css'
        assert opts[0].get('inline') is False

    def test_ie_conditional_comments(self):
        assert self.urls(b"""<!--[if IE 6]>
        <link href="home.css" rel="stylesheet" />
        <![endif]-->""") == ['http://example.org/home.css']

    def test_base(self):
        assert self.urls(b"""
        <base href="/bar/">
        <a href="foo">""") == ['http://example.org/bar/foo']

    def test_base_with_inline_css(self):
        assert self.urls(b"""
        <base href="http://elsdoerfer.name">
        <a style="background-image: url('foo.gif')">
        <style> background-image: url('bar.gif')</style>
        """) == ['http://elsdoerfer.name/foo.gif', 'http://elsdoerfer.name/bar.gif']

    def test_attrs_with_whitespace(self):
        assert self.urls(b"""<a href="
        /foo">""") == ['http://example.org/foo']

    def test_style_attribute(self):
        doc = b"""<html style="background-image: url('foo.png')">"""
        urls, opts = self.urls_with_opts(doc)
        assert urls[0] == 'http://example.org/foo.png'
        assert opts[0].get('inline') is True

        assert self.replace(doc, lambda u: 'bar.gif') == \
            b"""<html style="background-image: url('bar.gif')">"""

    def test_style_tag(self):
        doc = b"""<style>h1 { background-image: url('foo.png') }</style>"""
        urls, opts = self.urls_with_opts(doc)
        assert urls[0] == 'http://example.org/foo.png'
        assert opts[0].get('inline') is True

        assert self.replace(doc, lambda u: 'bar.gif') == \
            b"""<style>h1 { background-image: url("bar.gif") }</style>"""

    # HTML Tokenization issues

    def test_unclosed_style_tag(self):
        """[regression]"""
        doc = b"""<style>h1 { background-image: url('foo.png') }"""
        urls, opts = self.urls_with_opts(doc)
        assert urls[0] == 'http://example.org/foo.png'

    def test_linebreak_before_attr_value(self):
        """[regression]"""
        doc = b"""<a href=
        "foo">"""
        urls, opts = self.urls_with_opts(doc)
        assert urls[0] == 'http://example.org/foo'

    def test_opening_tag_not_closed(self):
        """[regression]"""
        doc = """<b<Das schön</b>Der eher"""
        tokens = self.parse(doc)
        assert tokens[0]['type'] == 'tag-open'
        assert tokens[0]['name'] == 'b<Das'
        assert tokens[1]['type'] == 'attr-begin'
        assert tokens[1]['name'] == 'schön<'
        assert tokens[2]['type'] == 'attr-begin'
        assert tokens[2]['name'] == 'b'
        assert tokens[3]['type'] == 'tag-open-end'
        assert len(tokens) == 4

