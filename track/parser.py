"""Contains classes to find the links in HTML, CSS etc. files.Parser

There are some minor design considerations that these classes are also
used during the "convert links" stage, so they cannot rely on knowledge
of the spidering process.
"""


import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin


class Parser(object):
    """Parse a file for links.
    """

    def __init__(self, data, url):
        self.data = data
        self.base_url = url

    def __iter__(self):
        yield from self._parse()

    def _parse(self):
        raise NotImplementedError()

    def replace_links(self, replacer):
        raise NotImplementedError


class HTMLParser(Parser):

    # An argument can possibly be made that this should be defined
    # by the spider instead?
    tags = {
        'a': {'attr': ['href']},
        'img': {'attr': ['href', 'src', 'lowsrc'], 'inline': True},
        'script': {'attr': ['src']},
        'link': {},

        'applet': {'attr': ['code'], 'inline': True},
        'bgsound': {'attr': ['src'], 'inline': True},
        'area': {'attr': ['href']},
        'body': {'attr': ['background'], 'inline': True},
        'embed': {'attr': ['src'], 'inline': True},
        'fig': {'attr': ['src'], 'inline': True},
        'frame': {'attr': ['src'], 'inline': True},
        'form': {},
        'iframe': {'attr': ['src'], 'inline': True},
        'input': {'attr': ['src'], 'inline': True},
        'layer': {'attr': ['src'], 'inline': True},
        'meta': {},
        'object': {'attr': ['data'], 'inline': True},
        'overlay': {'attr': ['src'], 'inline': True},
        'table': {'attr': ['background'], 'inline': True},
        'td': {'attr': ['background'], 'inline': True},
        'th': {'attr': ['background'], 'inline': True},
    }

    def replace_links(self, replacer):
        soup = BeautifulSoup(self.data)
        for url, setter in self._get_links_from_soup(soup):
            new_value = replacer(url)
            if new_value is not None:
                setter(new_value)
        return str(soup)

    def _parse(self):
        # TODO: Neither html.parser not html5lib doeskeep the specific
        # whitespace or quoting within attributes, possibly even order
        # is lost. If we want to write out a more exact replica of the
        # input, we need to look elsewhere (
        # lxml? replace based on line numbers?)
        # It also does not parse <!--[if IE]> comments.
        soup = BeautifulSoup(self.data)
        for url, setter in self._get_links_from_soup(soup):
            yield url

    def _get_links_from_soup(self, soup):
        # See if there is a <base> tag.
        base = soup.find('base')
        if base:
            base_url = base.get('href', '')
        else:
            base_url = self.base_url

        # Check tags that are known to have links of some sort.
        from .spider import URL
        for tag, options in self.tags.items():
            handler = getattr(self, '_handle_tag_{0}'.format(tag),
                              self._handle_tag)

            for element in soup.find_all(tag):
                for url, setter in handler(element, options):
                    # Make sure the url is absolute
                    url = urljoin(base_url, url)

                    # Put together a url object with all the info that
                    # we have ad that tests can use.
                    yield URL(url,
                              requisite=options.get('inline', False)), \
                          setter

    def _attr_setter(self, tag, attr_name):
        def setter(new_value):
            tag[attr_name] = new_value
        return setter

    def _handle_tag(self, tag, opts, **kwargs):
        """Generic tag processor. Extracts urls from opts['arg'].
        """
        for attr in opts.get('attr', []):
            url = tag.get(attr)
            if not url:
                continue
            yield url, self._attr_setter(tag, attr)

    def _handle_tag_link(self, tag, opts, **kwargs):
        """Handle the <link> tag. There are different types:

        References to other pages:

            <link rel="next" href="...">

        References to other types of urls:

            <link rel="alternate" type="application/rss+xml" href=".../?feed=rss2" />

        Requirements for the current page:

            <link rel="stylesheet" href="...">
            <link rel="shortcut icon" href="...">
        """
        url = tag.get('href')
        if not url:
            return
        rel = map(lambda s: s.lower(), tag.get('rel', []))
        is_inline = rel == ['stylesheet'] or 'icon' in rel   # TODO
        yield url, self._attr_setter(tag, 'href')

    def _handle_tag_form(self, tag, opts, **kwargs):
        """Handle the <form> tag.
        """
        # We currently skip forms completely. It might be worth looking
        # into our options here.
        yield from ()

    def _handle_tag_meta(self, tag, opts, **kwargs):
        """Handle the <meta> tag. Can look like this:

            <meta http-equiv="refresh" content="10; url=index.html">
            <meta name="robots" content="index,nofollow">

        Other types of meta tags we don't care about.
        """
        name = tag.get('name', '').lower()
        http_equiv = tag.get('http-equiv', '').lower()

        if name == 'robots':
            # TODO: Handle robot instructions
            pass

        elif http_equiv == 'refresh':
            content = tag.get('content', '')
            match = re.match(r'url=(.*)', content, re.IGNORECASE)
            if match:
                yield match.groups(0), self._attr_setter(tag, 'content')


class CSSParser(Parser):
    urltag_re = re.compile(r"""
        # url() expressions. real url() expressions support both types of
        # quotes ('") as well as no quotes at all. I didn't manage to
        # make all three cases work with a regular expression, so instead
        # we'll be stripping of surrounding quotes in code.
        # TODO: The problem with this: It won't match url(")")
        # It would be slower but simpler to use 3 expressions. Or we
        # might write a really simple parser.
        url\(
          # Some whitespace first
          \s*
          # Now the actual url
          (?P<url1>
            (?:
              # anything, except for a closing bracket and not
              # a backslash, which are handled separately.
              [^\\\)\n\r]
              |
              # Allow backslash to escape characters
              \\.
            )*
          )?
          \s*
        # closing bracket
        \)

        |

        # import rules that do not use url().
        @import
          # First some whitespace
          \s*
          # Then the opening quote
          (?P<quote>['"])
          # Now the actual url
          (?P<url2>
            # repeat (non-greedy, so we don't span multiple strings)
            (?:
              # anything, except not the opening quote, and not
              # a backslash, which are handled separately, and
              # no line breaks.
              [^\\\1]
              |
              # Allow backslash to escape characters
              \\.
            )*?
          )
          # same character as opening quote
          (?P=quote)
          # and allow some whitespace again
          \s*
        """, re.VERBOSE)

    def rewrite_url(self, m):
        # Get the regex matches; note how we maintain the exact
        # whitespace around the actual url; we'll indeed only
        # replace the url itself.
        text_before = m.groups()[0]
        url = m.groups()[1]
        text_after = m.groups()[2]

        # Normalize the url: remove quotes
        quotes_used = ''
        if url[:1] in '"\'':
            quotes_used = url[:1]
            url = url[1:]
        if url[-1:] in '"\'':
            url = url[:-1]

        url = self.replace_url(url) or url

        result = 'url(%s%s%s%s%s)' % (
            text_before, quotes_used, url, quotes_used, text_after)
        return result

    def _parse(self):
        # Remove comments first
        data = re.sub(r'(?s)/\*.*\*/', '', self.data)

        result = []
        def new_link(m):
            matches = m.groupdict()
            url = matches['url1'] or matches['url2']
            result.append(url)

        new_data = self.urltag_re.sub(new_link, data)
        yield from result

    def replace_links(self):
        raise NotImplementedError


class JavasSriptParser(Parser):

    def _parse(self):
        # No parsing JavaScript for now...
        yield from ()
