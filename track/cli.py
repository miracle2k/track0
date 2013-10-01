import numbers
import sys
from os.path import commonprefix, normpath, abspath
import argparse
from track.mirror import Mirror
from track.spider import Spider, Rules


class TestImpl(object):

    @staticmethod
    def default(url):
        """Special case for +/- defaults without test name.
        """
        return True

    @staticmethod
    def requisite(url):
        """Passes if the url is necessary to display a page that has
        been saved. This includes images, stylesheets script files, but
        also things that are more rare, like iframes or embeds.

        Note that this in effect functions recursively. If a HTML page
        links to a stylesheet, and the stylesheet defines a background
        image, then the image will pass as well.

        This is further special in that it is the one filter that is
        enabled by default. That is, the internal default ``@follow``
        rule is::

            - +requisite

        You can disable this easily:

            track URL @follow -requisite
        """
        return url.requisite

    @staticmethod
    def depth(url):
        """Tests the depth of the url within the discovery process. A
        starting url has a depth of 0, a link found within that
        starting url has a depth of 1, links found on that second page
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
        return url.depth

    @staticmethod
    def domain_depth(url):
        """This is like "depth" except that the counter resets after
        the domain changes while spidering. For example::

            track URL @follow +domain-depth=0

        Will download the first page of every external link on the page,
        but will not follow any internal links (where the depth would be
        1 for the first link found on a starting url).
        """
        return url.domain_depth

    @staticmethod
    def original_domain(url):
        """Passes urls that are on the same domain as the root url which
        was the starting point for the discovery of this url.

        The check runs before any duplicates are filtered out. This means
        that if there are two starting urls, ``a.com`` and ``b.com``, and
        ``a.com`` discovers ``b.com/foo`` before ``b.com`` itself does,
        the url *will* be followed the second time around.
        """
        return url.parsed.netloc == url.root.parsed.netloc

    @staticmethod
    def same_domain(url):
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
        if not url.previous:
            return True
        return url.parsed.netloc == url.previous.parsed.netloc

    @staticmethod
    def down(url):
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
        return TestImpl.path_distance_to_original(url) >= 0

    @staticmethod
    def path_level(url):
        """Test the depth of the path of an url.

        The path level of ``http:/example.org/`` is 0, the path level of
        ``http:/example.org/foo/`` is 1, and the path level of
        ``http:/example.org/foo/bar/`` is 2. However, the level of
        ``http:/example.org/foo/bar`` or ``http:/example.org/foo/bar.html``
        (i.e. no trailing slash) is 1.

        This is not to be confused with the "depth" test which checks the
        depth of the spidering process.
        """
        return len(url.parsed.path.split('/')) - 2

    @staticmethod
    def path_distance(url):
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
        # Short-circuit root urls
        if url.previous is None:
            return 0
        return TestImpl._path_distance(url, url.previous)

    @staticmethod
    def path_distance_to_original(url):
        """Like ``path-distance``, but tests the difference between the
        url and the original root url that was the starting point.

        A common use case is only following urls that are further down
        the hierarchy, which can be accomplished using::

            @follow +path-distance-to-original>=0

        Because it is so common, this test has a simple version available:

            @follow +down
        """
        # Short-circuit root urls
        if url.previous is None:
            return 0
        return TestImpl._path_distance(url, url.root)

    @staticmethod
    def _path_distance(url1, url2):
        # Test never passes if the domains have changed
        if url1.parsed.netloc != url2.parsed.netloc:
            return False

        source = url2.parsed.path.split('/')
        this = url1.parsed.path.split('/')
        shared = commonprefix([source, this])

        # /foo and /bar also will never pass
        if len(shared) < len(source) and len(shared) < len(this):
            return False

        return len(this) - len(source)


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
    'domain': TestImpl,
    'path': TestImpl,
    'filename': TestImpl,
    'extension': TestImpl,
    'querystring': TestImpl,
    'mimetype': TestImpl,
    'type': TestImpl,

    # Operating on URL metadata (headers)
    'content-type': TestImpl,
    'size': TestImpl,

    # Operating on the url/discovery source
    'tag': TestImpl,
    'requisite': TestImpl,
}


class OperatorImpl:
    @classmethod
    def _norm(cls, system_value, user_value):
        if isinstance(system_value, numbers.Number):
            try:
                user_value = int(user_value)
            except ValueError:
                user_value = False
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
    def truth(cls, a, b=None):
        assert not b
        return bool(a)

    @classmethod
    def equality(cls, a, b):
        a, b = cls._norm(a, b)
        return cls._same(a, b) and a == b

    @classmethod
    def smaller(cls, a, b):
        a, b = cls._norm(a, b)
        return cls._same(a, b) and a < b

    @classmethod
    def larger(cls, a, b):
        a, b = cls._norm(a, b)
        return cls._same(a, b) and a > b

    @classmethod
    def larger_or_equal(cls, a, b):
        a, b = cls._norm(a, b)
        return cls._same(a, b) and a >= b

    @classmethod
    def smaller_or_equal(cls, a, b):
        a, b = cls._norm(a, b)
        return cls._same(a, b) and a <= b


Operators = {
    '': OperatorImpl.truth,
    '=': OperatorImpl.equality,
    '<': OperatorImpl.smaller,
    '>': OperatorImpl.larger,
    '<=': OperatorImpl.larger_or_equal,
    '>=': OperatorImpl.smaller_or_equal
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
        test = self._get_test(test_name)
        if not test:
            raise RuleError('{0} is not a valid test'.format(test_name), rule)

        # The operator
        op = ''
        while stack and stack[0] in op_chars:
            op += stack.pop(0)

        # The rest is the value
        value = ''.join(stack)

        return (action, is_stop_action), test, op, value

    def _get_test(self, name):
        try:
            test = AvailableTests[name]
        except KeyError:
            return None

        if isinstance(test, type):
            # Allows multiple tests to be specified on a class
            name = name.replace('-', '_')
            return getattr(test, name, None)
        return test

    def _run_test(self, test, op, value, url):
        """Run a test, return True or False.
        """
        test_result = test(url)
        return Operators[op](test_result, value)

    def _apply_rules(self, rules, url):
        result = self.rule_default
        # We are are simply processing the rules from left to right, but
        # since the right-most rules take precedence, it would be smarter
        # to to the other direction. The reason we aren't doing that is
        # that ++/-- rules affect flow. We can probably re-arrange the
        # rules such that we can do the right thing *and* optimize.
        # TODO: Optimization is particularily important since some rules
        # cause a HEAD request, or worse, a full download.
        for (action, is_stop_action), test, op, value in rules:
            passes = self._run_test(test, op, value, url)
            if passes:
                result = action
                if is_stop_action:  # ++ or --
                    break
        return result

    def follow(self, url):
        return self._apply_rules(self.follow_rules, url)

    def save(self, url):
        return self._apply_rules(self.save_rules, url)

    def stop(self, url):
        return self._apply_rules(self.stop_rules, url)


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


def main(argv):
    parser = MyArgumentParser(argv[0], prefix_chars='-@')
    parser.add_argument('-O', '--path')
    parser.add_argument(
        'url', nargs='+', metavar='url',
        help='urls to be added to the queue initially as a starting point')
    parser.add_argument(
        '@follow', nargs='+', metavar='rule', default=['-'],
        help="rules that determine whether a url will be downloaded.")
    parser.add_argument(
        '@save', nargs='+', metavar='rule', default=['+'],
        help="rules that determine whether a url will be saved; default "
             "rule is '+', meaning everything that passes @follow is "
             "saved")
    parser.add_argument(
        '@stop', nargs='+', metavar= 'rule', default=['-'],
        help="rarely needed: rules that prevent a url from being analyzed"
             "for further links; default rule is '-' (never stop)")

    namespace = parser.parse_args(argv[1:])

    try:
        output_path = normpath(abspath(namespace.path or 'tracked'))
        mirror = Mirror(output_path)
        spider = Spider(CLIRules(namespace), mirror=mirror)
    except RuleError as e:
        print('error: {1}: {0}'.format(*e.args))
        return

    for url in namespace.url:
        spider.add(url)
    spider.loop()


def run():
    sys.exit(main(sys.argv) or 0)
