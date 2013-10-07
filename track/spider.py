from collections import deque
import requests
from urllib.parse import urlparse
from requests.exceptions import InvalidSchema, MissingSchema, ConnectionError
import urlnorm
from track.parser import HTMLParser, get_parser_for_mimetype


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

    def configure_session(self, request):
        """Allows configuring the general environment.
        """

    def configure_request(self, request, url):
        """Allows configuring the request for each url.
        """


class URL(object):
    """A URL to be processed.

    Knows various metadata like depth, source etc.
    """

    def __init__(self, url, previous=None, source=None, requisite=False,
                 **extra):
        self.url = urlnorm.norm(url)
        self.source = source
        self.requisite = requisite
        self.set_previous(previous)
        self.extra = extra

        # Runtime data
        self.exception = None
        self.retries = 0

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

    def resolve(self, type):
        """This actually executes a request for this URL.

        A ``session`` attribute with a ``requests`` session needs to
        be set for this to work. This session needs to be adorned
        with a ``configure_request()`` method, which would usually
        go to the :meth:`Rules.configure_request` hook.

        ``type`` specifies whether a HEAD request suffices, or if you
        need a full request. The trick is that this will cache the
        response, and return the cached response if possible.

        It can therefore be called by different parts of the system
        (the tests, the spider, the mirror) without concern for
        unnecessary network traffic.
        """
        assert type in ('head', 'full')
        if not hasattr(self, 'session'):
            raise TypeError(
                'This URL instance has no session, cannot resolve().')

        # TODO: We can optimize this further, no need to try a full
        # request if the head already returned an error. We might also
        # just try to raise the error all the way through the rule handling.
        if not hasattr(self, '_response') or (
                getattr(self, '_response_type') == 'head' and type == 'full'):
            try:
                method = 'GET' if type=='full' else 'HEAD'
                request = requests.Request(method, self.url).prepare()
                self.session.configure_request(request, self)
                self._response = self.session.send(request)
            except (InvalidSchema, MissingSchema):
                # Urls like xri://, mailto: and the like.
                self._response = False
                self.exception = None
            except ConnectionError as e:
                self._response = False
                self.exception = e
            finally:
                self._response_type = type

        return self._response

    def retry(self):
        self.retries += 1
        self.exception = None
        delattr(self, '_response')
        delattr(self, '_response_type')
        return self

    def __repr__(self):
        return '<URL {0}>'.format(self.url)


def get_content_type(response):
    """Helper that strips out things like ";encoding=utf-8".
    """
    return response.headers.get('content-type', '').split(';', 1)[0]


class Spider(object):
    """The main spider logic. Continuously download URLs and follow links.
    """

    max_retries = 5

    def __init__(self, rules, mirror=None):
        self._url_queue = deque()
        self._known_urls = []
        self.rules = rules
        self.mirror = mirror
        self.logger = Logger()

    def __len__(self):
        return len(self._url_queue)

    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = requests.Session()
            self.rules.configure_session(self._session)
            self._session.configure_request = self.rules.configure_request
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
        if url.url in self._known_urls:
            return

        # Attach a session to the url so it can resolve itself
        url.session = self.session

        # Test whether this is a link that we should even follow
        if url.source != 'user' and not self.rules.follow(url):
            return

        # Download the URL
        self.logger.url(url)
        response = url.resolve('full')
        if response is False:
            # This request failed at the connection stage
            if url.retries <= self.max_retries:
                url = url.retry()
                self._url_queue.appendleft(url)
            return

        # Do not follow errors
        if response.status_code >= 400:
            return

        # TODO: response.links contains links from http headers. Should we
        # do something with them?

        # Attach a link parser now, which will start to work when needed.
        # The mirror might need the links during save, or the spider when
        # the @stop rules pass. Or we might get away without parsing.
        parser_class = get_parser_for_mimetype(get_content_type(response))
        if parser_class:
            response.parsed = parser_class(response.text, response.url)
        else:
            response.parsed = None

        # Save the file locally?
        if self.mirror and self.rules.save(url):
            self.mirror.add(url, response)

        # No need to process this url again
        self._known_urls.append(url.url)

        # Run a hook that makes it possible to stop now and ignore
        # all the urls contained in this page.
        if self.rules.stop(url):
            return

        # Add all links
        for link, opts in response.parsed or ():
            # Put together a url object with all the info that
            # we have ad that tests can use.
            requisite = opts.pop('inline', False)
            try:
                link = URL(link, requisite=requisite, **opts)
            except urlnorm.InvalidUrl:
                continue
            link.set_previous(url)
            self._url_queue.appendleft(link)


