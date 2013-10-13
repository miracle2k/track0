"""Contains classes to find the links in HTML, CSS etc. files.Parser

There are some minor design considerations that these classes are also
used during the "convert links" stage, so they cannot rely on knowledge
of the spidering process.
"""

import contextlib
import re
import string
from urllib.parse import urljoin
from html5lib.inputstream import HTMLBinaryInputStream


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

    def as_bytes(self, data):
        if isinstance(data, bytes):
            return data
        return data.encode(self.encoding or 'utf-8')

    def as_text(self, data):
        if isinstance(data, str):
            return data
        return data.decode(self.encoding or 'utf-8')

    def same_as_input(self, data):
        if isinstance(self.data, bytes):
            return self.as_bytes(data)
        return self.as_text(data)

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

    @contextlib.contextmanager
    def jump(self, pos):
        old_pos = pos
        try:
            self.pos = pos
            yield
        finally:
            self.pos = old_pos

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

    def skip_while(self, *chars):
        chars = ''.join(chars)
        while self.cur() and self.cur() in chars:
            self.next()

    def skip_whitespace(self):
        return self.skip_while('\n\r\t ')

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


class HTMLTokenizer(Parser):
    """With a heavy heart, I'm implementing a very simple HTML scanner
    myself. The problem with all existing HTML parsers is that they cannot
    reproduce the original document.

    For example, html5lib follows the official reference implementation
    which doesn't care about this. However, we do. We'd really prefer our
    mirrored files to match the originals as closely as possible, because
    some users might.

    A normal parser would decode entities, reorder tags, reset quoting etc.
    They also usually insert document structure like a missing <html>
    or a missing closing tag, i.e. fixing tree errors.

    They also do not provide you with the byte position of tokens in the
    file; line/column yes, but certainly not the position of a tag attribute.
    Thus, we can't replace the links outside the parser either.

    Further, they wouldn't support conditional HTML comments, which we can
    more easily support in your own parser.

    We care about valid HTML even less than a regular browser does. We
    don't care that the spec says that U+0060 GRAVE ACCENT after an
    attribute name should be considered a parse error.

    We just need slightly more than a regex looking for ``http://`` urls.
    """

    asciiLetters = frozenset(string.ascii_letters)

    def _detect_encoding(self):
        # Use a simple way to detect the encoding beforehand. This will
        # try chardet and the BOM, but also scan some initial bytes for
        # a metatag.
        #
        # There seems to a spec'ed way to do a "changeEncoding" thing
        # during the parsing, and the html stream class has a
        # changeEncoding() method (called by the parser when a meta
        # encoding info is encountered). We  might want to use it.
        stream = HTMLBinaryInputStream(self.data)
        stream.defaultEncoding = ''
        encoding, certainty = stream.detectEncoding()
        return encoding

    def _parse(self):
        encoding = self.encoding or self._detect_encoding() or 'utf-8'
        p = ParserKit(self.data.decode(encoding))

        peek = p.peek
        cur = p.cur
        match = p.match
        next = p.next

        while cur():
            # TODO: character refs, IE comments
            # Loosely following the tokenization spec:
            #   http://dev.w3.org/html5/spec-LC/tokenization.html#data-state

            # Skip any comments. Don't even bother implementing strict
            # SGML comments, but do pay attention to IE conditionals.
            if match('<!--'):
                if not match('[if IE '):
                    while cur() is not None:
                        if match('-->'):
                            break
                        next()

            # A new tag
            if cur() == '<':
                # http://dev.w3.org/html5/spec-LC/tokenization.html#tag-open-state
                next()

                if cur() == '!':
                    # http://dev.w3.org/html5/spec-LC/tokenization.html#markup-declaration-open-state
                    # http://dev.w3.org/html5/spec-LC/tokenization.html#bogus-comment-state
                    p.skip_until('>')
                    continue

                # Only catch "true" open tags, let a free-standing < alone,
                # as per the spec. Also ignore closing tags etc.
                elif cur() in self.asciiLetters:
                    # http://dev.w3.org/html5/spec-LC/tokenization.html#tag-name-state
                    yield p.switch_element()
                    tag_name = p.skip_until('\t\r\n />')
                    yield p.switch_element(type='tag-open', name=tag_name)
                    tag_attrs = {}

                    # Read all attributes
                    while True:
                        # http://dev.w3.org/html5/spec-LC/tokenization.html#before-attribute-name-state
                        p.skip_whitespace()
                        if not cur() or cur() in '/>':
                            p.skip_while('/>')
                            break

                        # http://dev.w3.org/html5/spec-LC/tokenization.html#attribute-name-state
                        yield p.switch_element()
                        attr_name = p.skip_until('\t\r\n />=')
                        yield p.switch_element(
                            type='attr-begin', name=attr_name, tag_name=tag_name)

                        # Parse attribute value
                        if p.next_if('='):
                            yield p.switch_element()
                            # http://dev.w3.org/html5/spec-LC/tokenization.html#before-attribute-value-state
                            if cur() in '\'"':
                                # http://dev.w3.org/html5/spec-LC/tokenization.html#attribute-value-double-quoted-state
                                # http://dev.w3.org/html5/spec-LC/tokenization.html#attribute-value-single-quoted-state
                                quote_char = next()
                                attr_value = p.skip_until(quote_char)
                                next()
                            else:
                                # http://dev.w3.org/html5/spec-LC/tokenization.html#attribute-value-unquoted-state
                                attr_value = p.skip_until('\t\r\n >')

                            attr_value_token = p.switch_element(
                                    type='attr-value', value=attr_value,
                                    attr_name=attr_name, tag_name=tag_name)
                            yield attr_value_token

                            # Note: We purposefully do *not* exclude
                            # attr-value tokens for duplicate attributes,
                            # as the spec would require. We're trying
                            # to avoid missing files due to technicalities.
                            #
                            # But for the dict-version, use only the first
                            # value, as the spec requires.
                            if not attr_name in tag_attrs:
                                tag_attrs[attr_name] = attr_value_token

                    # For convenience, yield a token with all the attributes
                    yield p.switch_element(
                        type='tag-open-end', name=tag_name, attrs=tag_attrs)

                    # Done processing the opening tag.
                    # We now need to implement some special processing of
                    # tag contents that we care about: <style> and <script>.
                    #
                    # In the spec, the parser will switch the tokenizer to
                    # special states; the script data state seems to support
                    # HTML comments within the script tag, which we currently
                    # ignore (we don't find urls in script tags anyway); we
                    # treat both like the RAWTEXT state.
                    #
                    # http://dev.w3.org/html5/spec-LC/tokenization.html#rawtext-state
                    # http://www.w3.org/TR/html5/syntax.html#parsing-main-inhead
                    assert peek(-1) == '>' or cur() is None

                    if tag_name in ('style', 'script'):
                        yield p.switch_element()
                        while cur():
                            last_pos_before_match = p.pos

                            # http://dev.w3.org/html5/spec-LC/tokenization.html#rawtext-end-tag-name-state
                            if match('</{}'.format(tag_name)):
                                if cur() in ('\t\r\n />='):
                                    with p.jump(last_pos_before_match):
                                        yield p.switch_element(
                                            name=tag_name,
                                            type='tag-rawtext')
                                    break
                            next()

                    # do not skip a char
                    continue

            next()

        # Complete the final element
        yield p.switch_element()


class HTMLParser(HTMLTokenizer):
    """We've simply split the low-level parsing into the base class.
    """

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
        'style': {},
        'object': {'attr': ['data'], 'inline': True},
        'overlay': {'attr': ['src'], 'inline': True},
        'table': {'attr': ['background'], 'inline': True},
        'td': {'attr': ['background'], 'inline': True},
        'th': {'attr': ['background'], 'inline': True},
    }

    def replace_urls(self, replacer):
        elements = list(self._parse())

        for url, kwargs, setter in self._iter_urls(elements):
            if isinstance(url, Parser):
                parser = url
                setter(parser.replace_urls(replacer, **kwargs))
            else:
                new_url = replacer(self.absurl(url))
                if new_url:
                    setter(new_url)

        new_data = ''.join([el['data'] for el in elements])
        return self.as_bytes(new_data)

    def get_urls(self):
        elements = self._parse()
        for url, opts, _ in self._iter_urls(elements):
            if isinstance(url, Parser):
                parser = url
                for nested_url, opts in parser:
                    yield self.absurl(nested_url), opts
            else:
                yield self.absurl(url), opts

    def _iter_urls(self, elements):
        """Find the urls within the token stream.
        """
        elements = list(elements)

        # Search for the base tag first
        doc_base_url = None
        for e in elements:
            if e['type'] == 'attr-value' and e['attr_name'] == 'href' and e['tag_name'] == 'base':
                doc_base_url = e['value']
                break

        for element in elements:
            # See if this is an attribute we need to process
            if element['type'] == 'attr-value':
                tag, attr, value = \
                    element['tag_name'], element['attr_name'], element['value']

                # If it is a style attribute, return a nested parser
                if attr == 'style':
                    yield CSSParser(value, url=self.base_url), \
                          {'escape': 'single'}, \
                          self._mk_attr_setter(element)

                # See if this is in the list of attributes
                if not tag in self.tags:
                    continue
                if not attr in self.tags[tag].get('attr', []):
                    continue
                if not value:
                    continue

                url = value.strip()
                if doc_base_url:
                    url = urljoin(doc_base_url, url)
                options = {
                    'inline': self.tags[tag].get('inline', False),
                    'tag': '{0}.{1}'.format(tag, attr)
                }
                yield url, options, self._mk_attr_setter(element)

            # See if this is a tag we have a handler for
            if element['type'] == 'tag-open-end':
                tag, attrs = element['name'], element['attrs']
                if not tag in self.tags:
                    continue
                handler = getattr(self, '_handle_tag_{}'.format(tag), False)
                if not handler:
                    continue

                # Call the method that process this tag with a mapping
                # of the attribute tokens, and a simplified mapping of
                # attributes directly to values.
                handler_result = handler(
                    {a: attrs[a]['value'] for a in attrs},
                    attrs)

                # The return value is a 4-tuple
                for url, options, attr_name, setter in handler_result:
                    options.update({'tag': '{0}.{1}'.format(tag, attr_name)})
                    yield url.strip(), options, setter

            # See if this is tag content that we have a handler for
            if element['type'] == 'tag-rawtext':
                tag, data = element['name'], element['data']
                if not tag in self.tags:
                    continue
                handler = getattr(self, '_handle_text_{}'.format(tag), False)
                if not handler:
                    continue

                for subparser in handler(data):
                    # Do not wrap the <style> tag in quotes (quote=False)
                    yield subparser, {}, \
                          self._mk_attr_setter(element, quote=False)

    def _mk_attr_setter(self, element, quote='double'):
        """Return a function that will set a new value on a attr-value token.

        Can optionally wrap the value in quotes and escape it.
        """
        def setter(new_value):
            fmt = {'single': "'{}'", 'double': '"{}"', False: "{}"}[quote]
            if quote == 'single':
                new_value = new_value.replace("'", '&#39;')
            elif quote == 'double':
                new_value = new_value.replace('"', '&quot;')

            element['data'] = fmt.format(new_value)
        return setter

    def _handle_text_style(self, text):
        """Contents of a <style> tag."""
        yield CSSParser(text, url=self.base_url)

    def _handle_tag_link(self, attrs, tokens):
        """Handle the <link> tag. There are different types:

        References to other pages:

            <link rel="next" href="...">

        References to other types of urls:

            <link rel="alternate" type="application/rss+xml" href=".../?feed=rss2" />

        Requirements for the current page:

            <link rel="stylesheet" href="...">
            <link rel="shortcut icon" href="...">
        """
        url = attrs.get('href')
        if not url:
            return
        rel = attrs.get('rel')
        is_inline = self.is_inline_link_rel(rel)
        yield url, \
              {'inline': is_inline}, \
              'href', \
              self._mk_attr_setter(tokens['href'])

    def _handle_tag_form(self, attrs, tokens):
        """Handle the <form> tag.
        """
        url = attrs.get('action')
        if not url:
            return
        # Return the action url, but flag it as a no follow. The
        # spider won't download it, but the mirror will replace it.
        yield url, {'do-not-follow': True}, \
              'action', self._mk_attr_setter(tokens['action'])

    def _handle_tag_meta(self, attrs, tokens):
        """Handle the <meta> tag. Can look like this:

            <meta http-equiv="refresh" content="10; url=index.html">
            <meta name="robots" content="index,nofollow">

        Other types of meta tags we don't care about.
        """
        name = attrs.get('name', '').lower()
        http_equiv = attrs.get('http-equiv', '').lower()

        if name == 'robots':
            # TODO: Handle robot instructions
            pass

        elif http_equiv == 'refresh':
            content = attrs.get('content', '')
            match = re.match(r'url=(.*)', content, re.IGNORECASE)
            if match:
                # TODO: replacing this link is harder
                yield \
                    match.groups(0), \
                    {}, \
                    'content', \
                    self._mk_attr_setter(tokens['content'])

    @classmethod
    def is_inline_link_rel(cls, rel):
        """Check if this rel identifier refers to a link that should
        be treated as inline. Note that a rel may have multiple values.
        """
        rel = list(map(lambda s: s.strip(), rel.split(' ')))
        return 'stylesheet' in rel or 'icon' in rel


class CSSParser(Parser):
    """
    CSS charset detection should be:

        1. HTTP Charset header.
        2. Byte Order Mark.
        3. The first @charset rule.
        4. UTF-8.

    This currently doesn't do (2) or (3).
    """

    def replace_urls(self, replacer, escape='double'):
        elements = list(self._parse())

        for element in elements:
            if element['type'] == 'url':
                new_url = replacer(self.absurl(element['url']))
                if new_url:
                    if escape == 'single':
                        element['data'] = "'{0}'".format(new_url.replace("'", "\\'"))
                    elif escape == 'double':
                        element['data'] = '"{0}"'.format(new_url.replace('"', '\\"'))
                    else:
                        element['data'] = '{0}'.format(new_url)

        return self.same_as_input(''.join([el['data'] for el in elements]))

    def get_urls(self):
        elements = self._parse()
        for element in elements:
            if element['type'] == 'url':
                yield element['url'], {'inline': True}

    def _parse(self):
        p = ParserKit(self.as_text(self.data))

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
                'inline': HTMLParser.is_inline_link_rel(link.get('rel', ''))}
            yield link['url'], opts



def get_parser_for_mimetype(mimetype):
    if mimetype == 'text/html':
        return HTMLParser
    elif mimetype == 'text/css':
        return CSSParser
    return None
