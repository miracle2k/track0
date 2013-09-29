from collections import deque
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from requests.exceptions import InvalidSchema, MissingSchema


class Logger(object):

    def url(self, url):
        """Note a url being processed.
        """
        print('{0}{1}'.format(' '*url.depth, url.url))


class Rules(object):
    """Defines the logic of the spider: when to follow a URL,
    when to save a file locally etc.
    """

    def follow(self, url):
        """Return ``True`` if the spider should follow this URL.
        """
        raise NotImplementedError()

    def save(self, url):
        """Return ``True`` if the url should be saved locally.
        """
        raise NotImplementedError()

    def stop(self, page):
        """Return ``False`` if the urls of a page should not be followed.

        The difference to :meth:`follow` is that this runs after
        :meth:`save`.
        """

    def configure_request(self, request, url):
        """Allows configuring the request for each url.
        """


class DefaultRules(Rules):

    def follow(self, url):
        return True

    def save(self, url):
        return True

    def stop(self, page):
        return False


class URL(object):
    """A URL to be processed.

    Knows various metadata like depth, source etc.
    """

    def __init__(self, url, previous=None, source=None, requisite=False):
        self.url = url
        self.source = source
        self.requisite = requisite
        self.set_previous(previous)

    def set_previous(self, previous):
        """Set the url that is the source for this one.
            """
        self.previous = previous
        if previous:
            self.root = previous.root
            self.depth = previous.depth + 1
            self.domain_depth = 0 \
                if self.parsed.netloc != previous.parsed.netloc \
                else previous.domain_depth + 1
            if previous.requisite:
                self.requisite = True
        else:
            self.root = self
            self.depth = 0
            self.domain_depth = 0

    @property
    def history(self):
        if not hasattr(self, '_history'):
            history = []
            page = self
            while page is not None:
                history.append(page)
                page = page.previous
            self._history = history
        return self._history

    @property
    def parsed(self):
        if not hasattr(self, '_parsed'):
            self._parsed = urlparse(self.url)
        return self._parsed

    def __repr__(self):
        return '<URL {0}>'.format(self.url)


def get_content_type(response):
    """Helper that strips out things like ";encoding=utf-8".
    """
    return response.headers.get('content-type', '').split(';', 1)[0]


class Spider(object):
    """The main spider logic. Continuously download URLs and follow links.
    """

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
        'iframe': {'attr': ['src'], 'inline': True},
        'input': {'attr': ['src'], 'inline': True},
        'layer': {'attr': ['src'], 'inline': True},
        'object': {'attr': ['data'], 'inline': True},
        'overlay': {'attr': ['src'], 'inline': True},
        'table': {'attr': ['background'], 'inline': True},
        'td': {'attr': ['background'], 'inline': True},
        'th': {'attr': ['background'], 'inline': True},
    }

    def __init__(self, rules, mirror=None):
        self._url_queue = deque()
        self._known_urls = []
        self.rules = rules
        self.mirror = mirror
        self.logger = Logger()

    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = requests.Session()
        return self._session

    def add(self, url):
        """Add a new URL to be processed.
        """
        url_obj = URL(url, previous=None, source='user')
        self._url_queue.appendleft(url_obj)

    def loop(self):
        while len(self._url_queue):
            self.process_one()
        if self.mirror:
            self.mirror.finish()

    def process_one(self):
        url = self._url_queue.pop()

        # Test whether this is a link that we should even follow
        if url.source!='user' and not self.rules.follow(url):
            return

        # Download the URL
        self.logger.url(url)
        try:
            page = self.download(url)
        except (InvalidSchema, MissingSchema):
            # Urls like xri://, mailto: and the like.
            return
        page.url_obj = url

        # Save the file locally?
        if self.mirror and self.rules.save(url):
            self.mirror.add(page)

        # No need to process this url again
        self._known_urls.append(url.url)

        # Run a hook that makes it possible to stop now and ignore
        # all the urls contained in this page.
        if self.rules.stop(url):
            return

        # Add all links
        content_type = get_content_type(page)
        if content_type in ('text/html',):
            for url in self.parse(page):
                # TODO It is to early to check here, some urls may
                # pass the test under different circumstances.
                if url.url in self._known_urls:
                    continue
                self._url_queue.appendleft(url)

    def download(self, url):
        request = requests.Request('GET', url.url).prepare()
        self.rules.configure_request(request, url)
        return self.session.send(request)

    def parse(self, page):
        soup = BeautifulSoup(page.text)

        # See if there is a <base> tag.
        base = soup.find('base')
        if base:
            base_url = base.get('href', '')
        else:
            base_url = page.url_obj.url

        # Check tags that are known to have links of some sort.
        for tag, options in self.tags.items():
            handler = getattr(self, '_handle_tag_{0}'.format(tag),
                              self._handle_tag)

            for element in soup.find_all(tag):
                for url in handler(element, options, page=page):
                    # Make sure the url is absolute
                    url = urljoin(base_url, url)

                    # Put together a url object with all the info that
                    # we have ad that tests can use.
                    yield URL(
                        url, page.url_obj,
                        requisite=options.get('inline', False))


    def _handle_tag(self, tag, opts, **kwargs):
        """Generic tag processor. Extracts urls from opts['arg'].
        """
        for attr in opts.get('attr', []):
            url = tag.get(attr)
            if not url:
                continue
            yield url

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
        yield url

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
            match = re.match(r'url=(.*)', re.IGNORECASE)
            if match:
                yield match.groups(0)


