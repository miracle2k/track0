from collections import deque
import requests
from urllib.parse import urlparse
from requests.exceptions import InvalidSchema, MissingSchema
from track.parser import HTMLParser


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
    """This class controls the spider.
    """

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

        # Do not bother to process the same url twice
        # TODO: Account for user specificed url not being
        # in exactly the right format, for example missing
        # trailing slash.
        if url.url in self._known_urls:
            return

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

        # TODO: page.links contains links from http headers. Should we
        # do something with them?

        # Attach a link parser now, which will start to work when needed.
        # The mirror might need the links during save, or the spider when
        # the @stop rules pass. Or we might get away without parsing.
        page.parsed = HTMLParser(page.text, page.url)

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
            for link in page.parsed:
                link.set_previous(url)
                self._url_queue.appendleft(link)

    def download(self, url):
        request = requests.Request('GET', url.url).prepare()
        self.rules.configure_request(request, url)
        return self.session.send(request)

