from contextlib import closing
import hashlib
import inspect
import numbers
import shelve
import string
import sys
import fnmatch
from os.path import commonprefix, normpath, abspath, basename, splitext, join
import argparse
from track.mirror import Mirror
from track.spider import Spider, Rules, get_content_type, Events


class Redirect(Exception):
    """A test that detects a redirect and for this reason knows it cannot
    provide the right value would raise this.
    """


class TestImpl(object):

    @staticmethod
    def default(link):
        """Special case for +/- defaults without test name.
        """
        return True

    @staticmethod
    def requisite(link, ctx):
        """Passes if the url is necessary to display a page that has
        been saved. This includes images, stylesheets script files, but
        also things that are more rare, like iframes or embeds.
        """
        if not link.info.get('inline', False):
            return False

        if not link.previous:
            return False

        # The link that was inlining this must have been saved.
        #
        # You might question whether it is correct to intermix the concepts
        # of "url" and "link" here. I believe the answer is yes, because for
        # each unique url only one link will ever be processed anyway. I.e.
        # there is no way that previous.url will ever NOT be the one that
        # was saved to the mirror. So there is no way a different
        # previous.url could con us into accepting a requirement.
        if not link.previous.url in ctx['spider'].mirror.encountered_urls:
            return False

        return True

    @staticmethod
    def depth(link):
        """Tests the depth of the link within the discovery process. A
        starting link has a depth of 0, a link found within that
        starting link has a depth of 1, links found on that second page
        have a depth of 2.

        Note that you need to test using comparison operators. To go
        four levels deep, you would use::

            @follow +depth<=4

        But if we said instead::

            @follow +depth=4

        the rule would only match pages that have been discovered after
        following three previous links, and without other rules you would
        never get that far.
        """
        return link.depth

    @staticmethod
    def domain_depth(link):
        """This is like "depth" except that the counter resets after
        the domain changes while spidering. For example::

            track URL @follow +domain-depth=0

        Will download the first page of every external link on the page,
        but will not follow any internal links (where the depth would be
        1 for the first link found on a starting link).
        """
        return link.domain_depth

    @staticmethod
    def original_domain(link):
        """Passes urls that are on the same domain as the root url which
        was the starting point for the discovery of this url.

        The check runs before any duplicates are filtered out. This means
        that if there are two starting urls, ``a.com`` and ``b.com``, and
        ``a.com`` discovers ``b.com/foo`` before ``b.com`` itself does,
        the url *will* be followed the second time around.
        """
        return link.parsed.netloc == link.root.parsed.netloc

    @staticmethod
    def same_domain(link):
        """Passes urls that are on the same domain as the previous url
        where they were found.

        This is not the same as ``original-domain``. For, example,
        consider this rule::

            +same-domain +tag="link"

        This will cause the spider to at first remain on the starting
        domain, like ``original-domain`` would as well. But we also
        follow urls that we have discovered through <link> tags, and
        once a <link> tag leads to a different domain, further links
        on that domain are followed as well.
        """
        if not link.previous:
            return True
        return link.parsed.netloc == link.previous.parsed.netloc

    @staticmethod
    def down(link):
        """Passes urls that are further down the path hierarchy than
        the starting point. For example, given this command::

            $ track http://www.example.org/foo/bar @follow +down

        then ``http://www.example.org/foo/baz`` would pass, but
        ``http://www.example.org/qux`` would not, and neither would
        ``http://google.com``. That is, this is a more restrictive
        version of ``original-domain``.

        .. note::
            This is a handy shortcut for ``path-distance-to-original>0``.
            The ``path-distance-to-original`` gives you even more
            control, like controlling how deep to go. It even allows
            going upwards.
        """
        return TestImpl.path_distance_to_original(link) >= 0

    @staticmethod
    def path_level(link):
        """Test the depth of the path of an url.

        The path level of ``http:/example.org/`` is 0, the path level of
        ``http:/example.org/foo/`` is 1, and the path level of
        ``http:/example.org/foo/bar/`` is 2. However, the level of
        ``http:/example.org/foo/bar`` or ``http:/example.org/foo/bar.html``
        (i.e. no trailing slash) is 1.

        This is not to be confused with the "depth" test which checks the
        depth of the spidering process.
        """
        return len(link.parsed.path.split('/')) - 2

    @staticmethod
    def path_distance(link):
        """The path distance is the difference in the values as returned
        by ``path-level`` between the url, and the previous one::

            @follow +path-distance=1

        This means that going from ``http:/example.org/foo/`` to
        ``http:/example.org/foo/bar/`` is allowed, but going to
        ``http:/example.org/foo/index.html`` is not.

        The distance can be positive or negative.

        There is no distance between ``/foo/`` and ``/bar/`, nor is there
        a distance between two urls on different domains. The test will
        never pass in such cases.
        """
        # Short-circuit root links
        if link.previous is None:
            return 0
        return TestImpl._path_distance(link, link.previous)

    @staticmethod
    def path_distance_to_original(link):
        """Like ``path-distance``, but tests the difference between the
        url and the original root url that was the starting point.

        A common use case is only following urls that are further down
        the hierarchy, which can be accomplished using::

            @follow +path-distance-to-original>=0

        Because it is so common, this test has a simple version available:

            @follow +down
        """
        # Short-circuit root links
        if link.previous is None:
            return 0
        return TestImpl._path_distance(link, link.root)

    @staticmethod
    def _path_distance(link1, link2):
        # Test never passes if the domains have changed
        if link1.parsed.netloc != link2.parsed.netloc:
            return False

        source = link2.parsed.path.split('/')
        this = link1.parsed.path.split('/')
        shared = commonprefix([source, this])

        # /foo and /bar also will never pass
        if len(shared) < len(source) and len(shared) < len(this):
            return False

        return len(this) - len(source)

    @staticmethod
    def url(link):
        """Match against the full url, including query string.
        """
        return link.url

    @staticmethod
    def protocol(link):
        """Match against the protocol of the url.

        This will be something like ``http`` or ``https``.
        """
        return link.parsed.scheme

    @staticmethod
    def domain(link):
        """Match against the domain part of the url.

        For example, if the url is ``http://www.apple.com/iphone/``,
        then the domain will be ``http://www.apple.com``.
        """
        return link.parsed.netloc

    @staticmethod
    def port(link):
        """Match against the port of the url.

        For example, if the url is ``http://example.org:8080``, the port
        is ``8080``. You can run numeric comparisons again it (larger than,
        smaller than etc).

        If the url does not specify a port, ``80`` is used.
        """
        return link.parsed.port or 80

    @staticmethod
    def path(link):
        """Match against the path part of the url.

        For example, if the url is ``http://www.apple.com/iphone/``,
        then the path will be ``/iphone/``. At a minimum, the path
        will always be a single slash ``/``.
        """
        # "http://example.org" would return an empty string, do not
        # let that happen.
        return link.parsed.path or '/'

    @staticmethod
    def filename(link):
        """Match against the filename of a url.

        For example, if the url is ``http://example.org/foo/index.html``,
        the filename will be ``index.html``.

        If the url is ``http://example.org/foo/``, the filename will be
        empty. If the url is ``http://example.org/foo`` the filename will
        be ``foo``.
        """
        return basename(link.parsed.path)

    @staticmethod
    def extension(link):
        """Match against the file extension.

        For example, if the url is ``http://example.org/foo/index.html``,
        the extension will be ``html``.

        If there is no file extension, this test will match an empty string.
        """
        return splitext(basename(link.parsed.path))[1][1:]

    @staticmethod
    def querystring(link):
        """Match against the query string.

        For example, if the url is ``http://example.org/foo/?page=2&user=1``,
        the querystring will be ``page=2&user=1``.
        """
        return link.parsed.query

    @staticmethod
    def fragment(link):
        """Match against the link fragment.

        For example, if a link on the page is
        ``http://example.org/foo/#introduction``, the fragment will be
        ``introduction``.
        """
        return link.parsed.fragment

    @staticmethod
    def size(link):
        """Test the size of the document behind a url.

        You may use K, M or G as units::

            +size<1M

        Note: This will execute a HEAD request to the url to determine
        the size. If the HEAD request does not include information about
        the size, the full url needs to be fetched.
        """
        response = link.resolve('head')
        if not response:
            return None
        if response.redirects:
            raise Redirect()
        length = response.headers.get('content-length', None)
        if length is None:
            response = link.resolve('full')
            if not response:
                return None
            length = response.headers.get('content-length', None)
            if not length:
                # Force downloading the content
                length = len(response.content)
        return length


    @staticmethod
    def content_type(link):
        """Match against the content type of the url.

        A content type might be ``text/html`` or ``image/png``.

        Note: This will execute a HEAD request to the url to determine
        the content type.
        """
        response = link.resolve('head')
        if not response:
            return None
        return get_content_type(response)

    @staticmethod
    def content(link):
        """Match against the content of the url.

        Careful! This test requires a url to be downloaded in full .
        """
        response = link.resolve('full')
        if not response:
            return None
        return response.text

    @staticmethod
    def tag(link):
        """The tag and attribute where the url was found.

        For example, if the spider followed a standard link, this would
        return ``a.href``. Other possible values include, for example,
        ``img.src`` or ``script.src``.

        If the link was not found in a tag, this matches an empty string.
        """
        return link.extra.get('tag', '')


AvailableTests = {
    '': TestImpl.default,

    # Operating on the spidering process
    'depth': TestImpl,
    'domain-depth': TestImpl,

    # Operating on the relationship between urls
    'original-domain': TestImpl,
    'same-domain': TestImpl,
    'down': TestImpl,
    'path-level': TestImpl,
    'path-distance': TestImpl,
    'path-distance-to-original': TestImpl,

    # Operating on the URL itself
    'url': TestImpl,
    'protocol': TestImpl,
    'domain': TestImpl,
    'port': TestImpl,
    'path': TestImpl,
    'filename': TestImpl,
    'extension': TestImpl,
    'querystring': TestImpl,
    'fragment': TestImpl,

    # Operating on URL metadata (headers)
    'content-type': TestImpl,
    'size': TestImpl,
    'content': TestImpl,

    # Operating on the url/discovery source
    'tag': TestImpl,
    'requisite': TestImpl,
}


UNITS = {
    'G': 1000*1000*1000,
    'M': 1000*1000,
    'K': 1000
}


class OperatorImpl:
    @classmethod
    def _norm(cls, system_value, user_value):
        # If the system value is a number, treat the user value as one.
        if isinstance(system_value, numbers.Number):
            # If a prefix is attached, resolve it
            unit = None
            if user_value and user_value[-1].upper() in UNITS:
                user_value, unit = user_value[:-1], user_value[-1].upper()
            try:
                user_value = float(user_value)
            except ValueError:
                user_value = False
            else:
                if unit:
                    user_value = user_value * UNITS[unit]

        return system_value, user_value

    @classmethod
    def _same(cls, a, b):
        # Python matches 0==False, we don't want that though. This
        # will return False in such cases.
        # Stop Python matching 0 and False, even for in checks
        a = None if a is False else a
        b = None if b is False else b
        return (a is None and b is None) or (not a is None and not b is None)

    @classmethod
    def truth(cls, sys, user=None):
        assert not user
        return bool(sys)

    @classmethod
    def equality(cls, sys, user):
        if isinstance(sys, str) and isinstance(user, str):
            return fnmatch.fnmatch(sys, user)

        sys, user = cls._norm(sys, user)
        return cls._same(sys, user) and sys == user

    @classmethod
    def smaller(cls, sys, user):
        sys, user = cls._norm(sys, user)
        return cls._same(sys, user) and sys < user

    @classmethod
    def larger(cls, sys, user):
        sys, user = cls._norm(sys, user)
        return cls._same(sys, user) and sys > user

    @classmethod
    def larger_or_equal(cls, sys, user):
        sys, user = cls._norm(sys, user)
        return cls._same(sys, user) and sys >= user

    @classmethod
    def smaller_or_equal(cls, sys, user):
        sys, user = cls._norm(sys, user)
        return cls._same(sys, user) and sys <= user


Operators = {
    '': OperatorImpl.truth,
    '=': OperatorImpl.equality,
    '<': OperatorImpl.smaller,
    '>': OperatorImpl.larger,
    '<=': OperatorImpl.smaller_or_equal,
    '>=': OperatorImpl.larger_or_equal
}


UserAgents = {
    'chrome': 'Mozilla/5.0 (Windows NT 6.2; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/30.0.1599.17 Safari/537.36',
    'firefox': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:25.0) Gecko/20100101 Firefox/25.0',
    'ie': 'Mozilla/5.0 (compatible; MSIE 10.6; Windows NT 6.1; Trident/5.0; InfoPath.2; SLCC1; .NET CLR 3.0.4506.2152; .NET CLR 3.5.30729; .NET CLR 2.0.50727) 3gpp-gba UNTRUSTED/1.0',
    'safari': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_6_8) AppleWebKit/537.13+ (KHTML, like Gecko) Version/5.1.7 Safari/534.57.2'
}


class RuleError(Exception):
    pass


class CLIRules(Rules):
    """Makes the spider follow the rules defined in the argparse
    namespace given.
    """

    # This is the default of a rule pipeline. This is different
    # from the default the CLI might use if no rules are given.
    rule_default = False

    def __init__(self, arguments):
        self.arguments = arguments
        self.follow_rules = list(map(
            lambda f: self._parse_rule(f), arguments.follow))
        self.save_rules = list(map(
            lambda f: self._parse_rule(f), arguments.save))
        self.stop_rules = list(map(
            lambda f: self._parse_rule(f), arguments.stop))

    def _parse_rule(self, rule):
        """Parse a rule like ``+depth>3`` into a 4-tuple.
        """
        stack = list(rule)

        # The action prefix (+/-)
        if not stack[0] in ('+', '-'):
            raise RuleError('You need to explicitly prefix each rule with + or -', rule)
        action = stack.pop(0)
        is_stop_action = stack[:1] == [action]  # Double trouble: --, ++
        if is_stop_action:
            stack.pop(0)
        action = action == '+'

        op_chars = ('<', '>', '=', '~')

        # The test name
        test_name = ''
        while stack and stack[0] not in op_chars:
            test_name += stack.pop(0)
        # Check the test name now for an early error
        test = self.get_test(test_name)
        if not test:
            raise RuleError('{0} is not a valid test'.format(test_name), rule)

        # The operator
        op = ''
        while stack and stack[0] in op_chars:
            op += stack.pop(0)

        # The rest is the value
        value = ''.join(stack)

        return (action, is_stop_action), test, op, value

    @classmethod
    def get_test(cls, name):
        try:
            test = AvailableTests[name]
        except KeyError:
            return None

        if isinstance(test, type):
            # Allows multiple tests to be specified on a class
            name = name.replace('-', '_')
            return getattr(test, name, None)
        return test

    def _run_test(self, test, op, value, link, ctx):
        """Run a test, return True or False.
        """
        args = [link]
        if len(inspect.getargspec(test).args) == 2:
            args.append(ctx)
        test_result = test(*args)
        return Operators[op](test_result, value)

    def _apply_rules(self, rules, link, spider):
        result = self.rule_default
        ctx = {
            'spider': spider
        }
        # We are are simply processing the rules from left to right, but
        # since the right-most rules take precedence, it would be smarter
        # to to the other direction. The reason we aren't doing that is
        # that ++/-- rules affect flow. We can probably re-arrange the
        # rules such that we can do the right thing *and* optimize.
        # TODO: Optimization is particularily important since some rules
        # cause a HEAD request, or worse, a full download.
        for (action, is_stop_action), test, op, value in rules:
            try:
                passes = self._run_test(test, op, value, link, ctx)
                if passes:
                    result = action
                    if is_stop_action:  # ++ or --
                        break
            except Redirect:
                # If the test can't provide value to to the redirect,
                # evaluate the whole thing to "allow". We don't want
                # this single rule element to stand in the way of
                # following the redirect, the test will run again against
                # the proper url. This approach will do the right thing
                # for all cases:
                #   -size>1m -domain=foo.*
                #   +size>1m -domain=foo.*
                #   --size<3m +
                result = True
        return result

    def follow(self, link, spider):
        return self._apply_rules(self.follow_rules, link, spider)

    def save(self, link, spider):
        return self._apply_rules(self.save_rules, link, spider)

    def stop(self, link, spider):
        return self._apply_rules(self.stop_rules, link, spider)

    def configure_session(self, session):
        user_agent = UserAgents.get(
            self.arguments.user_agent, self.arguments.user_agent) or \
                     'Track/alpha'
        session.headers.update({
            'User-Agent': user_agent,
        })


class URLFormatter(string.Formatter):
    """Format a url into a filename using the tests.

    ::
        format('{domain}/{path}', url)
        format('{url|md5}', url)
    """

    def get_field(self, field_name, args, kwargs):
        link = args[0]

        # Parse the field name for filters. We can't use the standard
        # Python format() format and convert specs etc. because they
        # are too restrictive (e.g. only one character).
        field_name, *filters = (field_name+',').split(',', 2)

        # Run the test for the value
        test = CLIRules.get_test(field_name)
        value = test(link)

        # Normalize test result
        if value is None:
            value = ''
        elif value in (True, False):
            value = 'yes' if value else 'no'
        elif not isinstance(value, str):
            value = str(value)

        # Apply filters
        for fname in filter(bool, filters):
            if fname.isdigit():
                value = value[:int(fname)]
            elif fname == 'md5':
                value = hashlib.md5(value.encode()).hexdigest()

        return value, field_name


class CLIMirror(Mirror):
    """Customized mirror that follows the user's options.
    """

    @classmethod
    def read_info(cls, mirror_directory):
        """Load mirror info file w/o creating the mirror. This should
        not be necessary, and points to API design flaw in CLIMirror.
        """
        with closing(shelve.open(join(mirror_directory, '.track', 'info'))) as f:
            return dict(f)

    def __init__(self, namespace):
        output_path = normpath(abspath(namespace.path or 'tracked'))

        Mirror.__init__(
            self,
            output_path,
            write_at_once=not namespace.no_live_update,
            convert_links=not namespace.no_link_conversion)

        self.layout = namespace.layout
        self._url_formatter = URLFormatter()

    def get_filename(self, link, response):
        if self.layout:
            return self._url_formatter.format(self.layout, link)
        return Mirror.get_filename(self, link, response)


import colorama
colorama.init()

class CLIEvents(Events):

    def __init__(self):
        self.links = {}

    def added_to_queue(self, link):
        self.links.setdefault(link, {
            'follow': {},
            'save': {},
            'bail': {}
        })

    def taken_by_processor(self, link):
        self.added_to_queue(link)

        self._output_link(link)

    def follow_state_changed(self, link, **kwargs):
        self.added_to_queue(link)

        self.links.setdefault(link, {})
        self.links[link].setdefault('follow', {})
        self.links[link]['follow'].update(kwargs)

        self._output_link(link)

    def bail_state_changed(self, link, **kwargs):
        self.links.setdefault(link, {})
        self.links[link].setdefault('bail', {})
        self.links[link]['bail'].update(kwargs)

    def completed(self, link):
        self._output_link(link, True)

    def _output_link(self, link, finalize=False):
        state = self.links[link]['follow']

        standard = ''
        error = colorama.Fore.RED
        success = colorama.Fore.GREEN
        verbose = colorama.Fore.YELLOW
        style = standard

        # URL state/result identifier
        result = None
        if 'success' in state:
            result = ' + '
            style = success
        elif 'skipped' in state:
            if state['skipped'] == 'duplicate':
                result = 'dup'
                style = standard
            if state['skipped'] == 'rule-deny':
                result = ' - '
                style = verbose
        elif 'failed' in state:
            if state['failed'] == 'redirect':
                result = ' → '
                style = success
            if state['failed'] == 'http-error':
                result = 'err'
                style = error
            if state['failed'] == 'not-modified':
                result = '304'
                style = success
        if not result:
            result = '   '
            style = error

        # Number of links found
        num_links = self.links[link]['bail'].get('num_links', None)
        if num_links is not None:
            num_links = '\033[1m' + ' +{}'.format(num_links) + '\033[0m'
        else:
            num_links = ''

        msg = '{style}{result}{reset} {url}{num_links}'.format(
            style=style, reset=colorama.Style.RESET_ALL,
            result=result, url=link.original_url, num_links=num_links)

        import sys
        sys.stdout.write(msg +  ('\n' if finalize else '\r'))


class MyArgumentParser(argparse.ArgumentParser):

    class HelpFormatter(argparse.HelpFormatter):
        """argparse insists on adding positional arguments last on the
        usage string. Our special @-rule parsing can not reasonably allow
        anything following the rules; even "--" is taken. So we fix the
        usage string generation here. Ridiculously hacky.
        """
        def _format_usage(self, usage, actions, groups, prefix):
            # Set "option_strings" on our positional arguments. This makes
            # the base class sort them along with optional arguments
            # instead of moving them to the end of the list.
            for a in actions:
                if a.dest == 'url':
                    a.option_strings = ['url']
            return argparse.HelpFormatter._format_usage(self, usage, actions, groups, prefix)

        def _format_actions_usage(self, actions, groups):
            # While formatting the arguments, reset the option_strings
            # so they come out as expected.
            for a in actions:
                if a.dest == 'url':
                    a.option_strings = []
            return argparse.HelpFormatter._format_actions_usage(
                self, actions, groups)

    def __init__(self, *a, **kw):
        kw.setdefault('formatter_class', MyArgumentParser.HelpFormatter)
        super(MyArgumentParser, self).__init__(*a, **kw)
        self.__encountered_rule = False

    def _parse_optional(self, arg_string):
        klammeraffe = arg_string and arg_string[0] == '@'

        # @-arguments are used to specify filters.
        # Once a @ occurred, we only let other @-arguments through,
        # so that - and -- can be used along with + for the rules.
        if self.__encountered_rule and not klammeraffe:
            return None

        if klammeraffe:
            self.__encountered_rule = True

        return super(MyArgumentParser, self)._parse_optional(arg_string)


class Script:

    @classmethod
    def build_argument_parser(cls, prog=None):
        parser = MyArgumentParser(prog, prefix_chars='-@')
        # 1. We are only providing short-hand arguments for the most
        #    important arguments. For others, the readability of a long
        #    option is preferred. This is part of what makes httrack
        #    so hard to understand.
        # 2. short options use uppercase if they are significant
        #    environment-setup type options (like output path) as opposed
        #    to behaviour details (like the user agent).
        #
        # Affecting the local mirror
        parser.add_argument(
            '-O', '--path',
            help='output directory for the mirror')
        parser.add_argument(
            '-U', '--update', action='store_true',
            help="use the command line options previously used when an"
                 "existing mirror was created")
        parser.add_argument(
            '--layout',
            help='a custom layout for organizing the files in the target '
                 'directory; use tests as variables, e.g. {domain}')
        parser.add_argument(
            '--enable-delete', action='store_true',
            help='delete existing local files no encountered by the spider')
        parser.add_argument(
            '--no-link-conversion', action='store_true',
            help='do not modify urls in the local copy in any way')
        parser.add_argument(
            '--backups', action='store_true',
            help='will store an unmodified copy of each file in a ./backups '
                 'subfolder; unaffected by link conversion and deletion.')
        parser.add_argument(
            '--no-live-update', action='store_true',
            help='delay local mirror modifications until the spider is done')
        # Affecting the start urls
        parser.add_argument(
            '-F', '--from-file', action='append', metavar='FILE',
            help='Add urls from the file, one per line; can be given multiple times')
        parser.add_argument(
            'url', nargs='*', metavar='url',
            help='urls to be added to the queue initially as a starting point')
        # Affecting the parsing process
        parser.add_argument(
            '--user-agent',
            help="user agent string to use; the special values 'firefox', "
                 "'safari', 'chrome', 'ie' are recognized")

        parser.add_argument(
            '@follow', nargs='+', metavar='rule', default=['-', '+requisite'],
            help="rules that determine whether a url will be downloaded; default"
                 "is '- _requisite', meaning only the url itself and it's assets"
                 "are followed")
        parser.add_argument(
            '@save', nargs='+', metavar='rule', default=['+'],
            help="rules that determine whether a url will be saved; default "
                 "rule is '+', meaning everything that passes @follow is "
                 "saved")
        parser.add_argument(
            '@stop', nargs='+', metavar= 'rule', default=['-'],
            help="rarely needed: rules that prevent a url from being analyzed"
                 "for further links; default rule is '-' (never stop)")

        return parser

    @classmethod
    def get_default_namesspace(cls, **set):
        """Return an argparse namespace instance with empty attributes
        for all of our command line arguments. This is useful for
        interacting with the CLI classes in code.
        """
        parser = cls.build_argument_parser()

        # copied from argparse.py
        namespace = argparse.Namespace()

        # add any action defaults that aren't present
        for action in parser._actions:
            if action.dest is not argparse.SUPPRESS:
                if not hasattr(namespace, action.dest):
                    if action.default is not argparse.SUPPRESS:
                        setattr(namespace, action.dest, action.default)

        # add any parser defaults that aren't present
        for dest in parser._defaults:
            if not hasattr(namespace, dest):
                setattr(namespace, dest, parser._defaults[dest])

        # add user values
        for key, value in set.items():
            setattr(namespace, key, value)

        return namespace

    def main(self, argv):
        parser = self.build_argument_parser(argv[0])
        namespace = parser.parse_args(argv[1:])

        # Setup the mirror
        if namespace.update:
            if not CLIMirror.is_valid_mirror(namespace.path):
                print(('error: --update requested, but {} is not an '
                       'existing mirror').format(namespace.path))
                return

            info = CLIMirror.read_info(namespace.path)
            last_ns, cmdline = info['cli-ns'], ' '.join(info['cli-argv'])

            # Copy most attributes from the old namespace to ours
            # TODO: We should raise an error if there are any other
            # arguments besides --update and --path which affect the
            # the mirror output (i.e. number of threads would be ok).
            for attr in dir(last_ns):
                if attr.startswith('_'):
                    continue
                if attr in ['path']:
                    continue
                setattr(namespace, attr, getattr(last_ns, attr))

            # TODO: Currently includes options like -O which are are NOT
            # using from the stored copy. Will confuse the user.
            print('Using previous configuration: {}'.format(cmdline))
            print()


        # Open the local mirror
        mirror = CLIMirror(namespace)

        # Setup the spider
        try:
            spider = Spider(CLIRules(namespace), mirror=mirror, events=CLIEvents())
        except RuleError as e:
            print('error: {1}: {0}'.format(*e.args))
            return

        # Add the urls specified at the command line
        for url in namespace.url:
            spider.add(url)

        # Load urls from additional files specified
        for filename in namespace.from_file or ():
            with open(filename, 'r') as f:
                for url in f.readlines():
                    spider.add(url.strip())

        if not len(spider):
            parser.print_usage()
            print('error: I need at least one url to start with')
            return

        # Before we start, store the cli arguments in the mirror so
        # it can be updated without specifying them again.
        # TODO: Absolutize filenames before storing them.
        if not namespace.update:
            mirror.info['cli-ns'] = namespace
            mirror.info['cli-argv'] = argv[1:]

        # Go
        spider.loop()

        # If so desired, we can delete files from the mirror that no
        # longer exist online.
        # TODO: This needs to happen before Mirror.finish()
        if namespace.enable_delete:
            mirror.delete_unencountered()


def main(argv):
    Script().main(argv)


def run():
    sys.exit(main(sys.argv) or 0)
