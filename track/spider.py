from collections import deque
from itertools import chain
import requests
from urllib.parse import urlparse
from requests.exceptions import InvalidSchema, MissingSchema, ConnectionError, Timeout
import urlnorm
from track.parser import get_parser_for_mimetype, HeaderLinkParser


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

    def configure_session(self, session):
        """Allows configuring the general environment.
        """

    def configure_request(self, request, url):
        """Allows configuring the request for each url.
        """


class URL(object):
    """A URL to be processed.

    Knows various metadata like depth, source etc.
    """

    def __init__(self, url, previous=None, **info):
        self.url = urlnorm.norm(url)
        self.set_previous(previous)
        self.info = info

        # Runtime data
        self.session = None
        self.response = None
        self.exception = None
        self.retries = 0

    @property
    def source(self):
        return self.info.get('source')

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

    def resolve(self, type, etag=None, last_modified=None):
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

        # TODO: Consider just raising the error all the way through
        # the rule handling.

        # If we have already tried to resolve this url and there was an
        # error, don't bother again; that is, we skip the
        # upgrade-HEAD-to-GET logic.
        if (self.response and not self.response.ok) or self.exception:
            return self.response

        # Skip if the previous request is sufficient for the requested type
        # (i.e. not a HEAD response when we are asking for a full GET)
        if self.response is not None and (
                    self.response.request.method != 'HEAD' or type=='head'):
            return self.response

        try:
            method = 'GET' if type == 'full' else 'HEAD'
            request = requests.Request(method, self.url)
            if etag:
                request.headers['if-none-match'] = etag
            if last_modified:
                request.headers['if-modified-since'] = last_modified
            self.session.configure_request(request, self)

            request = request.prepare()
            response = self.session.send(
                request,
                # If the url is not saved and not a document, we don't
                # need to access the content. The question is:
                # TODO: Is it better to close() or to keep-alive?
                # This also affects redirects handling, if we don't close
                # we can't use the same connection to resolve redirects.
                stream=False,  # method=='GET'
                # Handle redirects manually
                allow_redirects=False)

            redirects = self.session.resolve_redirects(
                response, request,
                # Important: We do NOT fetch the body of the final url
                # (and hopefully `resolve_redirects` wouldn't waste any
                # time on a large intermediary url either). This is because
                # at this time we only care about the final url. If this
                # url is not to be processed, we will not have wasted
                # bandwidth.
                # TODO: Consider doing the redirect resolving using HEAD.
                stream=False)

            response.redirects = list(redirects)

            self.response = response
        except (InvalidSchema, MissingSchema):
            # Urls like xri://, mailto: and the like.
            self.response = False
            self.exception = None
        except (ConnectionError, Timeout) as e:
            self.response = False
            self.exception = e

        return self.response

    def retry(self):
        self.retries += 1
        self.exception = None
        self.response = None
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
    session_class = requests.Session

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
            self._session = self.session_class()
            self.rules.configure_session(self._session)
            self._session.configure_request = self.rules.configure_request
        return self._session

    def add(self, url, **kwargs):
        """Add a new URL to be processed.
        """
        opts = dict(previous=None, source='user')
        opts.update(kwargs)
        url_obj = URL(url, **opts)
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

        # Before we download that document, check if it is already in
        # the local mirror; if so, send along an etag or timestamp
        # that we might have.
        etag = modified = None
        if url.url in self.mirror.url_info:
            etag = self.mirror.url_info[url.url].get('etag') or False
            modified = self.mirror.url_info[url.url].get('last-modified') or False

        # Go ahead with the request
        self.logger.url(url)
        response = url.resolve('full', etag=etag, last_modified=modified)
        if response is False:
            # This request failed at the connection stage
            if url.retries <= self.max_retries:
                url = url.retry()
                self._url_queue.appendleft(url)
            return

        # If we have received a 304 not modified response, we are happy
        response_was_304 = response.status_code == 304
        if response.status_code == 304:
            print("304")

        # If we have been redirected to a different url, add that
        # url to the queue again.
        if response.redirects:
            redir_url = URL(
                response.redirects[-1].url, previous=url.previous, **url.info)
            self._url_queue.append(redir_url)
            response.close()

            # The mirror needs to know about the redirect. The status
            # code if the first redirect in a chain determines the type
            # (i.e. permanent, temporary etc)
            self.mirror.add_redirect(
                url, redir_url, response.status_code)
            return

        # Do not follow errors
        if response.status_code >= 400:
            return

        # Attach a link parser now, which will start to work when needed.
        # The mirror might need the links during save, or the spider when
        # the @stop rules pass. Or we might get away without parsing.
        parser_class = get_parser_for_mimetype(get_content_type(response))
        if parser_class:
            response.parsed = parser_class(response.text, response.url)
        else:
            response.parsed = None
        response.links_parsed = HeaderLinkParser(response)

        # Save the file locally?
        if self.mirror:
            if not response_was_304:
                if self.rules.save(url):
                    self.mirror.add(url, response)
            else:
                # Mirror still needs to know we found this url so
                # it won't be deleted during cleanup.
                self.mirror.encounter_url(url)

        # No need to process this url again
        self._known_urls.append(url.url)

        # Run a hook that makes it possible to stop now and ignore
        # all the urls contained in this page.
        if self.rules.stop(url):
            return

        # Process follow up links.
        #
        # If we have a "304 not modified response", then the mirror
        # can tell us the urls that this page is pointing to.
        if response_was_304:
            for link_url, info in self.mirror.url_info[url.url]['links']:
                link = URL(link_url, previous=url, **info)
                self._url_queue.appendleft(link)

        else:
            # Add links from the parsed content + the http headers
            for link, opts in chain(
                    response.links_parsed,
                    response.parsed or ()):
                # Put together a url object with all the info that
                # we have ad that tests can use.
                try:
                    link = URL(link, **opts)
                except urlnorm.InvalidUrl:
                    continue
                link.set_previous(url)
                self._url_queue.appendleft(link)


