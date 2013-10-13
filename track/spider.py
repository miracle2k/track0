from collections import deque
from itertools import chain
import requests
from urllib.parse import urlparse, urldefrag
from requests.exceptions import InvalidSchema, MissingSchema, ConnectionError, Timeout
import urlnorm
from track.parser import get_parser_for_mimetype, HeaderLinkParser


class Logger(object):


    def link(self, link):
        """Note a url being processed.
        """
        print('{0}{1}'.format(' '*link.depth, link.url))


class Rules(object):
    """Defines the logic of the spider: when to follow a link,
    when to save a file locally etc.
    """

    def follow(self, link, spider):
        """Return ``True`` if the spider should follow this link.
        """
        raise NotImplementedError()

    def save(self, link, spider):
        """Return ``True`` if the link should be saved locally.
        """
        raise NotImplementedError()

    def stop(self, page, spider):
        """Return ``False`` if the links of a page should not be followed.

        The difference to :meth:`follow` is that this runs after
        :meth:`save`.
        """

    def configure_session(self, session):
        """Allows configuring the general environment.
        """

    def configure_request(self, request, link):
        """Allows configuring the request for each link.
        """


class Link(object):
    """A url we encountered in the wild, to be processed.

    This class is called a "link", to emphasise the distinction we care
    about: The link is what we find out there, written by a user,
    not normalized, with case issues, containing a fragment etc. The
    link knows about where it was found (and at what spidering depth etc.)

    When we normalize the hell out of the link and disassociate it from
    the spidering process, we get a url.

    Documents A and B both have *links* to C; it is important for us to
    view those distinctly. But document C itself is represented by a
    unique url, even if both links look differently.

    Throughout the code base we try to enforce this distinction in
    variable and parameter names.
    """

    def __init__(self, url, previous=None, **info):
        # Normalize the url. This means case-sensitivity, and a whole
        # lot of other things that the urlnorm library will do for us.
        # It does also mean lossy operations though: The link may
        # contain an anchor; we need to maintain this anchor when we
        # put the url inside a locally saved copy, but we do not want
        # it to interfere with duplicate detection.
        self.original_url = urlnorm.norm(url)

        # For the normalized url that we'll be exposing, remove the
        # fragment, and treat https and http the same.
        url, fragment = urldefrag(self.original_url)
        self.lossy_url_data = {'fragment': fragment}
        if url.startswith('https:'):
            url = 'http' + url[5:]
            self.lossy_url_data = {'protocol': 'https'}
        self.url = url

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
        """Set the link that is the source for this one.
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
            self._parsed = urlparse(self.original_url)
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
                'This Link instance has no session, cannot resolve().')

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
        return '<Link {0}>'.format(self.url)


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
        self._link_queue = deque()
        self._known_urls = []
        self.rules = rules
        self.mirror = mirror
        self.logger = Logger()

    def __len__(self):
        return len(self._link_queue)

    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = self.session_class()
            self.rules.configure_session(self._session)
            self._session.configure_request = self.rules.configure_request
        return self._session

    def add(self, url, **kwargs):
        """Add a new Link to be processed.
        """
        opts = dict(previous=None, source='user')
        opts.update(kwargs)
        link = Link(url, **opts)
        self._link_queue.appendleft(link)

    def loop(self):
        while len(self._link_queue):
            self.process_one()
        if self.mirror:
            self.mirror.finish()

    def process_one(self):
        link = self._link_queue.pop()

        # Do not bother to process the same url twice
        if link.url in self._known_urls:
            return

        # Attach a session to the url so it can resolve itself
        link.session = self.session

        # Test whether this is a link that we should even follow
        if link.source != 'user' and not self.rules.follow(link, self):
            return

        # Before we download that document, check if it is already in
        # the local mirror; if so, send along an etag or timestamp
        # that we might have.
        etag = modified = None
        if link.url in self.mirror.url_info:
            etag = self.mirror.url_info[link.url].get('etag') or False
            modified = self.mirror.url_info[link.url].get('last-modified') or False

        # Go ahead with the request
        self.logger.link(link)
        response = link.resolve('full', etag=etag, last_modified=modified)
        if response is False:
            # This request failed at the connection stage
            if link.retries <= self.max_retries:
                link = link.retry()
                self._link_queue.appendleft(link)
            return

        # If we have received a 304 not modified response, we are happy
        response_was_304 = response.status_code == 304
        if response.status_code == 304:
            print("304")

        # If we have been redirected to a different url, add that
        # url to the queue again.
        if response.redirects:
            redir_link = Link(
                response.redirects[-1].url, previous=link.previous, **link.info)
            self._link_queue.append(redir_link)
            response.close()

            # The mirror needs to know about the redirect. The status
            # code if the first redirect in a chain determines the type
            # (i.e. permanent, temporary etc)
            self.mirror.add_redirect(
                link, redir_link, response.status_code)
            return

        # Do not follow errors
        if response.status_code >= 400:
            return

        # Attach a link parser now, which will start to work when needed.
        # The mirror might need the links during save, or the spider when
        # the @stop rules pass. Or we might get away without parsing.
        parser_class = get_parser_for_mimetype(get_content_type(response))
        if parser_class:
            response.parsed = parser_class(response.content, response.url,
                                           encoding=response.encoding)
        else:
            response.parsed = None
        response.links_parsed = HeaderLinkParser(response)

        # Save the file locally?
        if self.mirror:
            if not response_was_304:
                if self.rules.save(link, self):
                    self.mirror.add(link, response)
            else:
                # Mirror still needs to know we found this url so
                # it won't be deleted during cleanup.
                self.mirror.encounter_url(link)

        # No need to process this url again
        self._known_urls.append(link.url)

        # Run a hook that makes it possible to stop now and ignore
        # all the urls contained in this page.
        if self.rules.stop(link, self):
            return

        # Process follow up links.
        #
        # If we have a "304 not modified response", then the mirror
        # can tell us the urls that this page is pointing to.
        if response_was_304:
            for link_url, info in self.mirror.url_info[link.url]['links']:
                link = Link(link_url, previous=link, **info)
                self._link_queue.appendleft(link)

        else:
            # Add links from the parsed content + the http headers
            for url, opts in chain(
                    response.links_parsed,
                    response.parsed or ()):
                # Put together a url object with all the info that
                # we have ad that tests can use.
                try:
                    new_link = Link(url, **opts)
                except urlnorm.InvalidUrl:
                    continue
                new_link.set_previous(link)
                self._link_queue.appendleft(new_link)


