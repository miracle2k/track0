from collections import deque
import datetime
import email
from itertools import chain
import mimetypes
import reppy.cache
import re
import requests
from requests.models import Response
from urllib.parse import urlparse, urldefrag
from requests.exceptions import ConnectionError, Timeout, TooManyRedirects
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
        try:
            self.original_url = urlnorm.norm(url)
        except urlnorm.InvalidUrl as e:
            raise urlnorm.InvalidUrl('{}: {}'.format(e, url))

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

    def resolve(self, spider, type):
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
            spider.rules.configure_request(request, self, spider)

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
        except (TooManyRedirects):
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


class LocalFile(Link):
    """A locally-loaded file that can be added to the queue.
    """

    def __init__(self, content, url, filename=None, **more):
        super().__init__(url, **more)
        self.content = content
        self.filename = filename

    def resolve(self, spider, type):
        content = self.content
        if hasattr(content, 'read'):
            content = self.content.read()

        # Return a fake response
        response = Response()
        response._content = content
        response.url = self.url
        response.status_code = 200
        response.redirects = []

        # Determine a mimetype
        for name in (self.url, self.filename):
            guessed_type = mimetypes.guess_type(name)[0]
            if guessed_type and get_parser_for_mimetype(guessed_type):
                found_type = guessed_type
                break
        else:
            # Assume a HTML file
            found_type = 'text/html'
        response.headers['content-type'] = found_type

        return response

    def __repr__(self):
        return '<LocalFile {0}>'.format(self.url)


def get_content_type(response):
    """Helper that strips out things like ";encoding=utf-8".
    """
    return response.headers.get('content-type', '').split(';', 1)[0]


def parse_http_date_header(datestr):
    if not datestr:
        return None
    # http://stackoverflow.com/a/1472336/15677
    parsed = email.utils.parsedate(datestr)
    if not parsed:
        # TODO: Log this in very verbose mode
        return None
    return datetime.datetime(*parsed[:6])


class RobotsCache(reppy.cache.RobotsCache):

    def allowed(self, url):
        # Automatically use our user-agent.
        super().allowed(
            url, self.session.headers['user-agent'])


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
        return False

    def skip_download(self, link, spider):
        """Return ``True`` if the link should not be downloaded.

        This does not affect the flow. The database of links from
        the mirror will be used to process the link.
        """
        return False

    def configure_session(self, session, spider):
        """Allows configuring the general environment.
        """

    def configure_request(self, request, link, spider):
        """Allows configuring the request for each link.
        """


class DefaultRules(Rules):
    """Implement some sensible default behaviour.
    """

    def expiration_check(self, link, spider):
        if not link.url in spider.mirror.url_info:
            return False

        # An expires header allows us to skip completely.
        expires = spider.mirror.url_info[link.url].get('expires')
        if expires and expires > datetime.datetime.utcnow():
            return 'not-expired'

    def skip_download(self, link, spider):
        return self.expiration_check(link.url, spider)

    def configure_session(self, session, spider):
        session.headers.update({
            'User-Agent': 'Track/alpha',
        })

    def configure_request(self, request, link, spider):
        etag = last_modified = False
        if link.url in spider.mirror.url_info:
            etag = spider.mirror.url_info[link.url].get('etag') or False
            last_modified = spider.mirror.url_info[link.url].get('last-modified') or False
        if etag:
            request.headers['if-none-match'] = etag
        if last_modified:
            request.headers['if-modified-since'] = last_modified


class Spider(object):
    """The main spider logic. Continuously download URLs and follow links.
    """

    max_retries = 5
    session_class = requests.Session

    def __init__(self, rules, mirror=None, events=None):
        self._link_queue = deque()
        self._known_urls = set()
        self.rules = rules
        self.mirror = mirror
        self.events = events or Events()

    def __len__(self):
        return len(self._link_queue)

    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = self.session_class()
            self.rules.configure_session(self._session, self)
        return self._session

    @property
    def robots(self):
        """Exposes an object that allows querying the robots.txt
        file for a url. Will auto-fetch robots files and cache them.
        """
        if not hasattr(self, '_robots'):
            self._robots = RobotsCache(session=self.session)
        return self._robots

    def add(self, url, **kwargs):
        """Add a new Link to be processed.
        """
        if isinstance(url, Link):
            link = url
        else:
            # It is possible to attach extra data to a link using a {}
            # syntax, for example for a local file:
            #    input.html{http://example.org}
            url, data = re.match(r'^(.*?)(?:\{([^}]*)\})?$', url).groups()

            # Options for this link
            opts = dict(previous=None, source='user')
            opts.update(kwargs)

            if not urlparse(url).scheme:
                # This appears to be a local file
                with open(url, 'rb') as f:
                    link = LocalFile(f.read(), filename=url, url=data, **opts)
            else:
                link = Link(url, **opts)
        self._link_queue.appendleft(link)

    def _add(self, url, **opts):
        """Internal add-to-queue which refuses duplicates.
        """
        try:
            link = Link(url, **opts)
        except urlnorm.InvalidUrl:
            return

        # Check the normalized version of the url against the database
        if link.url in self._known_urls:
            return False

        self._link_queue.appendleft(link)
        self.events.added_to_queue(link)
        return True

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

        # Give the rules the option to skip the download, relying
        # on the information in the mirror instead.
        skip_download = self.rules.skip_download(link, self)

        if not skip_download:
            # Go ahead with the request
            response = link.resolve(self, 'full')
            if response is False:
                # This request failed at the connection stage
                if link.exception:
                    if link.retries <= self.max_retries:
                        link = link.retry()
                        # TODO: Log specific reason more clearly...
                        self.events.follow_state_changed(link, failed='connect-error')
                        return True
                    return False
                else:
                    self.events.follow_state_changed(link, failed='redirect-error')
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

        else:
            # We did not download this url
            self.events.follow_state_changed(link, failed='not-expired')
            response = False

        # Attach a link parser now, which will start to work when needed.
        # The mirror might need the links during save, or the spider when
        # the @stop rules pass. Or we might get away without parsing.
        if response and not response_was_304:
            parser_class = get_parser_for_mimetype(get_content_type(response))
            if parser_class:
                response.parsed = parser_class(response.content, response.url,
                                               encoding=response.encoding)
            else:
                response.parsed = None
            response.links_parsed = HeaderLinkParser(response)

        # Save the file locally?
        add_to_known_list = True
        if self.mirror:
            if not skip_download and not response_was_304:
                if isinstance(link, LocalFile):
                    # Local files are used as starting points only, they
                    # are not saved or otherwise treated as real.
                    self.events.save_state_changed(link, saved=False)
                    add_to_known_list = False
                elif self.rules.save(link, self):
                    self.mirror.add(link, response)
                    self.events.save_state_changed(link, saved=True)
                else:
                    self.events.save_state_changed(link, saved=False)
                    # TODO: This means that if a save rule is used, we will
                    # re-download duplicate urls during the follow phase.
                    # Maybe the duplicate check should happen at the
                    # follow level. But then a rule like @save depth=3 would
                    # not be reliable.
                    # Possible solution: move the save test up, before we
                    # do our regular fetch. We can then
                    add_to_known_list = False
            else:
                # Mirror still needs to know we found this url so
                # it won't be deleted during cleanup.
                self.mirror.encounter_url(link)

        # No need to process this url again
        if add_to_known_list:
            self._known_urls.add(link.url)

        # Run a hook that makes it possible to stop now and ignore
        # all the urls contained in this page.
        if self.rules.stop(link, self):
            self.events.bail_state_changed(link, bail=True)
            return

        # Process follow up links.
        #
        # If we didn't properly download a full response, then the mirror
        # can tell us the urls that this page is pointing to.
        num_links_followed = num_links_total = 0
        if skip_download or response_was_304:
            for link_url, info in self.mirror.url_info[link.url]['links']:
                num_links_total += 1
                if self._add(link_url, previous=link, **info):
                    num_links_followed += 1

        else:
            # Add links from the parsed content + the http headers
            for link_url, opts in chain(
                    response.links_parsed,
                    response.parsed or ()):
                # Put together a url object with all the info that
                # we have ad that tests can use.
                num_links_total += 1
                if self._add(link_url, previous=link, **opts):
                    num_links_followed += 1

        self.events.bail_state_changed(
            link, bail=False,
            links_followed=num_links_followed, links_total=num_links_total)


