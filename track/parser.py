"""Contains classes to find the links in HTML, CSS etc. files.Parser

There are some minor design considerations that these classes are also
used during the "convert links" stage, so they cannot rely on knowledge
of the spidering process.
"""


import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin


class Parser(object):
    """Base class for what we call a parser, but is really an interface
    to do two things: (1) find urls in a file and (2) return the file
    content with certain urls replaced.

    Some parsers will need to support both byte and string input, while
    others may be fine with either one, depending on what use cases they
    need to provide.
    However, it has to always return the urls as decoded strings, as well
    as in absolute form.
    This base class provides some tools to help with that.
    """

    def __init__(self, data, url, encoding=None):
        self.data = data
        self.base_url = url
        self.encoding = encoding

    def absurl(self, url):
        return urljoin(self.base_url, url)

    def __iter__(self):
        for url, opts in self.get_urls():
            yield self.absurl(url), opts

    def get_urls(self):
        raise NotImplementedError()

    def replace_urls(self, replacer):
        """Call replacer for each url. Use the return value as the
        new url. Return the modified data as a bytes stream.
        """
        raise NotImplementedError()


class HTMLParser(Parser):

    # An argument can possibly be made that this should be defined
    # by the spider instead?
    tags = {
        'a': {'attr': ['href']},
        'img': {'attr': ['href', 'src', 'lowsrc'], 'inline': True},
        'script': {'attr': ['src'], 'inline': True},
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

    def replace_urls(self, replacer):
        soup = BeautifulSoup(self.data)
        for url, options, setter in self._get_links_from_soup(soup):
            new_value = replacer(self.absurl(url))
            if new_value is not None:
                setter(new_value)
        return str(soup)

    def get_urls(self):
        # TODO: Neither html.parser not html5lib doeskeep the specific
        # whitespace or quoting within attributes, possibly even order
        # is lost. If we want to write out a more exact replica of the
        # input, we need to look elsewhere (
        # lxml? replace based on line numbers?)
        # It also does not parse <!--[if IE]> comments.
        # Further, we are losing things like &nbsp; entities, which
        # at the very least complicate encoding issues we have with
        # the local mirror.
        soup = BeautifulSoup(self.data)
        for url, options, setter in self._get_links_from_soup(soup):
            yield url, options

    def _get_links_from_soup(self, soup):
        # See if there is a <base> tag.
        base = soup.find('base')
        base_url = base.get('href', None) if base else None

        # Check tags that are known to have links of some sort.
        for tag, options in self.tags.items():
            handler = getattr(self, '_handle_tag_{0}'.format(tag),
                              self._handle_tag)

            for element in soup.find_all(tag):
                for url, opts, (tag, attr) in handler(element, options):
                    if base_url:
                        url = urljoin(base_url, url)
                    options.update(opts)
                    options['tag'] = '{0}.{1}'.format(tag.name, attr)
                    yield url, options, self._attr_setter(tag, attr)

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
            yield url, {}, (tag, attr)

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
        rel = list(map(lambda s: s.lower(), tag.get('rel', [])))
        is_inline = self.is_inline_link_rel(rel)
        yield url, {'inline': is_inline}, (tag, 'href')

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
                # TODO: replacing this link is harder
                yield match.groups(0), {}, (tag, 'content')

    @classmethod
    def is_inline_link_rel(cls, rel):
        """Check if this rel identifier refers to a link that should
        be treated as inline. Note that a rel may have multiple values
        so this must be passed as a list.
        """
        assert isinstance(rel, (list, tuple))
        return rel == ['stylesheet'] or 'icon' in rel


class ParserKit:
    """This is a very basic character lexer.

    The key method is :meth:`switch_element`.
    """
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.element = None
        self.switch_element()

    def next(self, num=1):
        self.pos += num
        return self.peek(-num)

    def peek(self, num=1):
        return self.data[self.pos+num] if self.pos<len(self.data)-num else None

    def cur(self):
        return self.peek(0)

    def match(self, text):
        result = text == self.data[self.pos:self.pos+len(text)]
        if result:
            self.next(len(text))
            return True
        return False

    def next_if(self, c):
        if self.cur() == c:
            self.next()
            return True
        return False

    def skip_whitespace(self):
        while self.cur() in '\n\r\t ':
            self.next()

    def skip_until(self, *chars, escape_chr=None):
        chars = ''.join(chars)
        result = ''
        quoted = False
        while self.cur() is not None:
            if self.cur() == escape_chr:
                self.next()
                quoted = True
                continue
            if quoted:
                result += self.next()
                quoted = False
            elif not self.cur() in chars:
                result += self.next()
            else:
                break
        return result

    def switch_element(self, **attrs):
        """Helps the parser serialize the whole file into a series of
        elements; we don't call them nodes, because they are not
        hierarchical.

        This is based on the idea that most of the file will be returned
        as an *unknown* node that we are not familiar with, and a few
        that we do care about (e.g. urls).

        One element is always active. When this method is called, the
        current element is finished, and a new element is made current.

        The previous element is returned, and knows at which position
        in the file it was started, and where it ends.
        """
        old_element = self.element
        self.element = {'type': 'unknown', 'pos': self.pos}
        if old_element:
            old_element.update(attrs)
            old_element['data'] = self.data[old_element['pos']:self.pos]
            return old_element


class CSSParser(Parser):

    def replace_urls(self, replacer):
        elements = list(self._parse())

        for element in elements:
            if element['type'] == 'url':
                new_url = replacer(self.absurl(element['url']))
                if new_url:
                    element['data'] = '"{0}"'.format(new_url.replace('"', '\\"'))

        return ''.join([el['data'] for el in elements])

    def get_urls(self):
        elements = self._parse()
        for element in elements:
            if element['type'] == 'url':
                yield element['url'], {'inline': True}

    def _parse(self):
        p = ParserKit(self.data)

        peek = p.peek
        cur = p.cur
        match = p.match
        next = p.next

        while cur():
            # Skip comments
            if cur() == '/' and peek() == '*':
                next(2)
                while cur() is not None and (cur() != '*' or peek() != '/'):
                    next(2)

            # @import without url()
            if match('@import'):
                p.skip_whitespace()
                if cur() in '"\'':
                    # Have the element include the quotes, for we need to
                    # be able to properly escape a replacement url.
                    yield p.switch_element()
                    quote_chr = next()

                    # Find the actual url
                    url = p.skip_until(quote_chr+'\n\r', escape_chr='\\')

                    # If there is a closing quote, include it
                    p.next_if(quote_chr)
                    yield p.switch_element(type='url', url=url)
                continue

            # url() instructions
            if match('url('):
                p.skip_whitespace()

                # Start new element with opening quote included
                yield p.switch_element()
                quote_chr = None
                if cur() in '"\'':
                    quote_chr = next()

                # Find the actual url
                url = p.skip_until(quote_chr or ')', '\n\r', escape_chr='\\')

                # If there is a closing quote, include it
                # (a closing bracket is not included).
                if quote_chr:
                    p.next_if(quote_chr)
                yield p.switch_element(type='url', url=url)
                continue

            next()

        # Complete the final element
        yield p.switch_element()


class JavasScriptParser(Parser):

    def _parse(self):
        # No parsing JavaScript for now...
        yield from ()


class HeaderLinkParser(Parser):
    """Not a real parser. It just returns the links from the headers
    of a http response in the same format as other parsers.
    """

    def __init__(self, response):
        self.response = response
        Parser.__init__(self, None, response.url)

    def get_urls(self):
        for link in self.response.links.values():
            opts = {
                'source': 'http-header',
                'inline': HTMLParser.is_inline_link_rel(
                    link.get('rel', '').split(' '))}
            yield link['url'], opts



def get_parser_for_mimetype(mimetype):
    if mimetype == 'text/html':
        return HTMLParser
    elif mimetype == 'text/css':
        return CSSParser
    return None
