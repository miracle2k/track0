from collections import deque
from itertools import chain
import requests
from urllib.parse import urlparse, urldefrag
from requests.exceptions import InvalidSchema, MissingSchema, ConnectionError, Timeout
import urlnorm
from track.parser import get_parser_for_mimetype, HeaderLinkParser



class Events(object):
    """This is the status notification interface for the spider.

    A link can either be:
        on the queue, currently in a processor.

    The processor can put it back on the queue.

    while the processor is running it can attach state to the link.

    TODO: Do we need a separate incremental log which woud include
      details about tests resolving, sending requests (HEAD etc, calc
      filename, save); possibly, this could all work through the events
      interface.

    """
    def added_to_queue(self, link):
        """This is called when a link is added to the queue.

        This will usually be a new link, but it is also possible that a
        processor returns a link to the queue to be tried again later.
        """

    def taken_by_processor(self, link):
        """Called when a links goes to a processor to be downloaded
        and saved.
        """

    def completed(self, link):
        """Called when a processor is done with the link.
        """

    def follow_state_changed(self, link, **kwargs):
        """Notification by the processor in the follow stage.

        This will contain information about whether a link will be
        downloaded, and whether that succeeds.

        Except dicts like these::

            {'skipped': 'duplicate'}
            {'failed': 'http-error', response: <response object>}

        Note that in theory, multiple such notifications with different
        keys may be sent, and it is up to you to make sense of it.

        For example, it is entirely possible that you'll get a
        ``failed=connection-error`` update, the link is added back to
        the queue, and yields a ``skipped=rule-deny`` event the next time.
        """


    def save_state_changed(self, link, **kwargs):
        """Notification by the processor about the save stage.

        At this point, the link has already been downloaded. This will
        let you know about the saving process.
        """
        pass

    def bail_state_changed(self, link, **kwargs):
        """Notification by the processor about the bail stage.

        Among other things, this will receive a status update informing
        you of the number of links found::

            {'num_links': 100}
        """


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

    def resolve(self, spider, type, etag=None, last_modified=None):
        """This actually executes a request for this URL.

        ``type`` specifies whether a HEAD request suffices, or if you
        need a full request. The trick is that this will cache the
        response, and return the cached response if possible.

        It can therefore be called by different parts of the system
        (the tests, the spider, the mirror) without concern for
        unnecessary network traffic.
        """
        assert type in ('head', 'full')

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
            spider.rules.configure_request(request, self)

            request = request.prepare()
            response = spider.session.send(
                request,
                # If the url is not saved and not a document, we don't
                # need to access the content. The question is:
                # TODO: Is it better to close() or to keep-alive?
                # This also affects redirects handling, if we don't close
                # we can't use the same connection to resolve redirects.
                stream=False,  # method=='GET'
                # Handle redirects manually
                allow_redirects=False)

            redirects = spider.session.resolve_redirects(
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

    def __init__(self, rules, mirror=None, events=None):
        self._link_queue = deque()
        self._known_urls = []
        self.rules = rules
        self.mirror = mirror
        self.events = events or Events()

    def __len__(self):
        return len(self._link_queue)

    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = self.session_class()
            self.rules.configure_session(self._session)
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
        self.events.taken_by_processor(link)
        add_again = self._process_link(link)
        self.events.completed(link)
        if add_again:
            self._link_queue.appendleft(link)
            self.events.added_to_queue(link)

    def _process_link(self, link):
        # Some links we are not supposed to follow, like <form action=>
        if link.info.get('do-not-follow'):
            self.events.follow_state_changed(link, skipped='no-download')
            return

        # Do not bother to process the same url twice
        if link.url in self._known_urls:
            self.events.follow_state_changed(link, skipped='duplicate')
            return

        # Test whether this is a link that we should even follow
        if link.source != 'user' and not self.rules.follow(link, self):
            self.events.follow_state_changed(link, skipped='rule-deny')
            return

        # Before we download that document, check if it is already in
        # the local mirror; if so, send along an etag or timestamp
        # that we might have.
        etag = modified = None
        if link.url in self.mirror.url_info:
            etag = self.mirror.url_info[link.url].get('etag') or False
            modified = self.mirror.url_info[link.url].get('last-modified') or False

        # Go ahead with the request
        response = link.resolve(self, 'full', etag=etag, last_modified=modified)
        if response is False:
            # This request failed at the connection stage
            if link.retries <= self.max_retries:
                link = link.retry()
                self.events.follow_state_changed(link, failed='connect-error')
                return True

            return False

        # If we have been redirected to a different url, add that
        # url to the queue again.
        if response.redirects:
            redir_link = Link(
                response.redirects[-1].url, previous=link.previous, **link.info)
            self._link_queue.append(redir_link)
            self.events.added_to_queue(link)
            response.close()

            # The mirror needs to know about the redirect. The status
            # code if the first redirect in a chain determines the type
            # (i.e. permanent, temporary etc)
            self.mirror.add_redirect(
                link, redir_link, response.status_code)

            self.events.follow_state_changed(link, failed='redirect')
            return

        # Do not follow errors
        if response.status_code >= 400:
            self.events.follow_state_changed(link, failed='http-error')
            return

        # If we have received a 304 not modified response, we are happy
        response_was_304 = response.status_code == 304
        if response_was_304:
            self.events.follow_state_changed(link, failed='not-modified')
        else:
            self.events.follow_state_changed(link, success=True)

        # Attach a link parser now, which will start to work when needed.
        # The mirror might need the links during save, or the spider when
        # the @stop rules pass. Or we might get away without parsing.
        if not response_was_304:
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
                    self.events.save_state_changed(link, saved=True)
                else:
                    self.events.save_state_changed(link, saved=False)
            else:
                # Mirror still needs to know we found this url so
                # it won't be deleted during cleanup.
                self.mirror.encounter_url(link)

        # No need to process this url again
        self._known_urls.append(link.url)

        # Run a hook that makes it possible to stop now and ignore
        # all the urls contained in this page.
        if self.rules.stop(link, self):
            self.events.bail_state_changed(link, bail=True)
            return

        # Process follow up links.
        #
        # If we have a "304 not modified response", then the mirror
        # can tell us the urls that this page is pointing to.
        num_links = 0
        if response_was_304:
            for link_url, info in self.mirror.url_info[link.url]['links']:
                try:
                    new_link = Link(link_url, previous=link, **info)
                except urlnorm.InvalidUrl:
                    continue
                self._link_queue.appendleft(new_link)
                self.events.added_to_queue(new_link)
                num_links += 1

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
                self.events.added_to_queue(new_link)
                num_links += 1

        self.events.bail_state_changed(link, bail=False, num_links=num_links)


