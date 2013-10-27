from collections import namedtuple, Counter
from contextlib import closing
import hashlib
import inspect
import numbers
import shelve
import string
import sys
import fnmatch
from os.path import normpath, abspath, join
import argparse
import blessings
from .mirror import Mirror
from .spider import Spider, DefaultRules, Events
from .tests import AvailableTests, Redirect
from .utils import ShelvedCookieJar, RefuseAll, NoneDict, ElasticString, BlessedString, BetterTerminal


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
    def inequality(cls, sys, user):
        return not cls.equality(sys, user)

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
    '!=': OperatorImpl.inequality,
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


Rule = namedtuple(
    'Rule', ['action', 'is_stop_action', 'test', 'op', 'value', 'pretty'])


class CLIRules(DefaultRules):
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

        op_chars = ('<', '>', '!', '=', '~')

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

        return Rule(action, is_stop_action, test, op, value, rule)

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
        test_results = []
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
        for rule in rules:
            try:
                passes = self._run_test(
                    rule.test, rule.op, rule.value, link, ctx)
                if passes:
                    result = rule.action
                    if rule.is_stop_action:  # ++ or --
                        break
                test_results.append((passes, rule))
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
                test_results.append((True, rule))
        return result, test_results

    def follow(self, link, spider):
        result, tests = self._apply_rules(self.follow_rules, link, spider)
        spider.events.follow_state_changed(link, tests=tests)
        return result

    def save(self, link, spider):
        result, tests = self._apply_rules(self.save_rules, link, spider)
        spider.events.save_state_changed(link, tests=tests)
        return result

    def stop(self, link, spider):
        result, tests = self._apply_rules(self.stop_rules, link, spider)
        spider.events.bail_state_changed(link, tests=tests)
        return result

    def skip_download(self, link, spider):
        if not link.url in spider.mirror.url_info:
            return False

        if self.arguments.no_modified_check:
            return 'exists'

        if self.arguments.trust_expires:
            return self.expiration_check(link, spider)

    def configure_session(self, session, spider):
        super().configure_session(session, spider)

        # Overwrite our default user agent with the user's choice
        user_agent = UserAgents.get(
            self.arguments.user_agent, self.arguments.user_agent)
        if user_agent:
            session.headers.update({
                'User-Agent': user_agent,
            })

        # Install a cookie jar
        cookie_shelve = spider.mirror.open_shelve('cookies')
        if self.arguments.cookies in ('persist',):
            session.cookies = ShelvedCookieJar(cookie_shelve)
        else:
            if not self.arguments.cookies == 'block':
                session.cookies.update(cookie_shelve)
            if self.arguments.cookies == 'refuse':
                session.cookies.policy = RefuseAll()


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


class CLIEvents(Events):

    def __init__(self, arguments, stream=None):
        self.arguments = arguments
        self.term = BetterTerminal(stream)
        self.stream = self.term.stream

        self.links = {}
        self.stats = Counter({'in_queue': 0, 'saved': 0})

    def init_db(self, link):
        self.links.setdefault(link, {
            'follow': NoneDict(),
            'save': NoneDict(),
            'bail': NoneDict()
        })

    def added_to_queue(self, link):
        self.init_db(link)
        self.stats['in_queue'] += 1

    def taken_by_processor(self, link):
        self.update_processor_status(link)

    def follow_state_changed(self, link, **kwargs):
        self.links[link]['follow'].update(kwargs)
        self.update_processor_status(link)

    def save_state_changed(self, link, **kwargs):
        self.links[link]['save'].update(kwargs)
        if kwargs.get('saved'):
            self.stats['saved'] += 1

        self.update_processor_status(link)

    def bail_state_changed(self, link, **kwargs):
        self.links[link]['bail'].update(kwargs)
        self.update_processor_status(link)

    def completed(self, link):
        self.display_link_completed(link)
        self.stats['in_queue'] -= 1

    def update_processor_status(self, link):
        raise NotImplementedError()

    def display_link_completed(self, link):
        raise NotImplementedError()

    def finalize(self):
        raise NotImplementedError()

    def _format_link(self, link):
        follow_state = self.links[link]['follow']
        save_state = self.links[link]['save']
        bail_state = self.links[link]['bail']

        t = self.term
        standard = self.term.normal
        error = self.term.red
        success = self.term.green
        verbose = self.term.yellow

        status_style = standard
        url_style = ''

        # URL state/result identifier
        result = None
        if 'success' in follow_state or follow_state['failed'] == 'not-modified':
            if follow_state['success']:
                result = ' + '
                if save_state['saved']:
                    result = ' ⚑ '
            else:
                result = '304'

            status_style = success
            if save_state['saved']:
                url_style = t.bold
            elif save_state['saved'] is False:
                url_style = t.bright_yellow

        elif 'skipped' in follow_state:
            if follow_state['skipped'] == 'duplicate':
                result = 'dup'
                status_style = standard
                return
            if follow_state['skipped'] == 'rule-deny':
                result = ' - '
                status_style = verbose
        elif 'failed' in follow_state:
            if follow_state['failed'] == 'redirect':
                result = ' → '
                status_style = success
            elif follow_state['failed'] in ('http-error', 'connect-error'):
                result = 'err'
                status_style = error
                url_style = error
            elif follow_state['failed'] == 'not-modified':
                pass
            else:
                url_style = error
        if not result:
            result = '   '
            status_style = error

        # Number of links found
        if bail_state['bail']:
            num_links = t.standout('[bail]')
        else:
            num_links = bail_state['links_followed']
            total_links = bail_state['links_total']
            if num_links is not None:
                num_links = ' +{}/{}'.format(t.string('bold', str(num_links)), total_links)
            else:
                num_links = ''

        # The last test that passed
        def analyze_tests(tests):
            last_test = ''
            passed_tests = [test for passed, test in (tests or []) if passed]
            if passed_tests:
                last_test = passed_tests[-1]
                last_test = t.string(
                    t.bright_green if last_test.action else error, last_test.pretty)
            return last_test
        follow_test = analyze_tests(follow_state['tests'])
        save_test = analyze_tests(save_state['tests'])

        test_state = ''
        if follow_test:
            test_state = ' @' + follow_test
        if save_test and self.arguments.save != ['+']:
            test_state += ' @' + save_test

        return ElasticString(
            t.string(status_style, result+' '),
            ElasticString.elastic(t.string(url_style, link.original_url)),
            num_links,
            test_state
        )


class LiveLogEvents(CLIEvents):
    """Log all links sequentially, continue updating each line
    as the status changes.
    """

    def update_processor_status(self, link):
        msg = self._format_link(link)
        if not msg:
            return

        # Write new version of the link status line
        self.stream.write(msg.format(self.term.width))
        # Move to next line, output spider status
        self.stream.write(self.term.move_down)
        self.stream.write('  [{0[in_queue]} queued, {0[saved]} files saved, ? downloaded]'.format(self.stats))
        # Move back
        self.stream.write('\033M')
        self.stream.write('\r')

    def display_link_completed(self, link):
        msg = self._format_link(link)
        if not msg:
            return

        # Write the final version of the link
        self.stream.write(msg.format(self.term.width))
        # Move to last line, which currently has the spider status
        self.stream.write(self.term.move_down)
        # Clear the spider status line
        self.stream.write(self.term.clear_eol)

    def finalize(self):
        # Move below both active lines
        self.stream.write(self.term.move_down)
        self.stream.write(self.term.move_down)


class CurrentProcessorsView(CLIEvents):
    """Once we support multiple processors, this would be designed to
    have one line for each processor at the bottom.

    When a processors finishes a link, it is logged above the processor
    status area, and the processor status area moves a line further below.

    This differs from :class:`LiveLogEvents`, where each link has it's
    own line, which is updated, but the line itself does not move.
    Therefore, with LiveLogEvents, if one processor takes very long, you
    could imagine that line scrolling outside the screen (or even
    the buffer?).
    """


class SequentialEvents(CLIEvents):
    def update_processor_status(self):
        pass
    def display_link_completed(self):
        pass


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

        # Affecting the start urls
        urls_group = parser.add_argument_group('starting urls')
        urls_group.add_argument(
            '-F', '--from-file', action='append', metavar='FILE',
            help='Add urls from the file, one per line; can be given multiple times')
        urls_group.add_argument(
            'url', nargs='*', metavar='url',
            help='urls to be added to the queue initially as a starting point')

        # Affecting the local mirror
        mirror_group = parser.add_argument_group('local mirror')
        mirror_group.add_argument(
            '-O', '--path',
            help='output directory for the mirror')
        mirror_group.add_argument(
            '--layout',
            help='a custom layout for organizing the files in the target '
                 'directory; use tests as variables, e.g. {domain}')
        mirror_group.add_argument(
            '--no-link-conversion', action='store_true',
            help='do not modify urls in the local copy in any way')
        mirror_group.add_argument(
            '--backups', action='store_true',
            help='will store an unmodified copy of each file in a ./backups '
                 'subfolder; unaffected by link conversion and deletion.')
        mirror_group.add_argument(
            '--no-live-update', action='store_true',
            help='delay local mirror modifications until the spider is done')

        # How to deal with existing files
        update_group = parser.add_argument_group('updating a mirror')
        update_group.add_argument(
            '-U', '--update', action='store_true',
            help="use the command line options previously used when an"
                 "existing mirror was created")
        update_group.add_argument(
            '--enable-delete', action='store_true',
            help='delete existing local files no encountered by the spider')
        update_group.add_argument(
            '--no-modified-check', action='store_true',
            help='do not check if an existing file has been modified on the '
                 'server')
        update_group.add_argument(
            '--trust-expires', action='store_true',
            help='skip checking files for updates if the expires header allows')

        # Affecting the UA behaviour, browsing process
        browing_group = parser.add_argument_group('browsing options')
        browing_group.add_argument(
            '--user-agent',
            help="user agent string to use; the special values 'firefox', "
                 "'safari', 'chrome', 'ie' are recognized")
        browing_group.add_argument(
            '--cookies', choices=('persist', 'accept', 'refuse', 'block'),
            default='persist',
            help="how to deal with cookies; persist = on disk for next time,"
                 "accept = forget when finished, refuse = no not accept new"
                 "cookies, but use previous cookies from disk, block = "
                 "additionally ignore disk cookies")

        rules_group = parser.add_argument_group('rules')
        rules_group.add_argument(
            '@follow', nargs='+', metavar='rule', default=['-', '+requisite'],
            help="rules that determine whether a url will be downloaded; default"
                 "is '- _requisite', meaning only the url itself and it's assets"
                 "are followed")
        rules_group.add_argument(
            '@save', nargs='+', metavar='rule', default=['+'],
            help="rules that determine whether a url will be saved; default "
                 "rule is '+', meaning everything that passes @follow is "
                 "saved")
        rules_group.add_argument(
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
            spider = Spider(CLIRules(namespace),
                            mirror=mirror, events=LiveLogEvents(namespace))
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
        try:
            spider.loop()
        except:
            # Be sure the console cursor is set such that
            # nothing will be overwritten
            spider.events.finalize()
            raise

        # If so desired, we can delete files from the mirror that no
        # longer exist online.
        # TODO: This needs to happen before Mirror.finish()
        if namespace.enable_delete:
            mirror.delete_unencountered()


def main(argv):
    Script().main(argv)


def run():
    sys.exit(main(sys.argv) or 0)
