from track.parser import CSSParser


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
