from math import ceil, floor
import blessings


class UserString(str):
    """Base for a custom str subclass.

    What this does is it intercepts the calls to string functions like
    upper(), and makes sure the subclass itself is returned.

    Adapted from jinja2/markupsafe.
    """

    def _clone(self, string):
        """Overwrite this if your string subclass cannot be instantiated
        using the standard string constructor.
        """
        return self.__class__(string)

    def __add__(self, other):
        if isinstance(other, str):
            return self._clone(super(UserString, self).__add__(other))
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, str):
            return self._clone(other.__add__(self))
        return NotImplemented

    def join(self, seq):
        return self._clone(str.join(self, seq))
    join.__doc__ = str.join.__doc__

    def split(self, *args, **kwargs):
        return list(map(self._clone, str.split(self, *args, **kwargs)))
    split.__doc__ = str.split.__doc__

    def rsplit(self, *args, **kwargs):
        return list(map(self._clone, str.rsplit(self, *args, **kwargs)))
    rsplit.__doc__ = str.rsplit.__doc__

    def splitlines(self, *args, **kwargs):
        return list(map(self._clone, str.splitlines(self, *args, **kwargs)))
    splitlines.__doc__ = str.splitlines.__doc__

    def make_wrapper(name):
        orig = getattr(str, name)
        def func(self, *args, **kwargs):
            return self._clone(orig(self, *args, **kwargs))
        func.__name__ = orig.__name__
        func.__doc__ = orig.__doc__
        return func

    for method in '__getitem__', 'capitalize', \
                  'title', 'lower', 'upper', 'replace', 'ljust', \
                  'rjust', 'lstrip', 'rstrip', 'center', 'strip', \
                  'translate', 'expandtabs', 'swapcase', 'zfill':
        locals()[method] = make_wrapper(method)

    # new in python 2.5
    if hasattr(str, 'partition'):
        def partition(self, sep):
            return tuple(map(self.__class__,
                             str.partition(self, self.escape(sep))))
        def rpartition(self, sep):
            return tuple(map(self.__class__,
                             str.rpartition(self, self.escape(sep))))

    # new in python 2.6
    if hasattr(str, 'format'):
        format = make_wrapper('format')

    # not in python 3
    if hasattr(str, '__getslice__'):
        __getslice__ = make_wrapper('__getslice__')

    del method, make_wrapper


class BlessedString(UserString):
    """A string wrapped in formatting instructions. Can wrap other
    BlessedStrings::

        BlessedString('blue',
            'before', BlessedString('green', 'content'), 'after')

    This will output the following string::

            {blue}before{green}content{blue}after{reset}
    """
    def _clone(self, *strings):
        # Do not allow modifying BlessedStrings that are based on
        # multiple parts (i.e. outer BlessedStrings). For example,
        # if we were to slice a BlessedString(part1, BlessedString(part2)),
        # we couldn't maintain the nested formatting.
        assert len(self.__parts) == 1 and not \
            isinstance(self.__parts[0], BlessedString)
        return self.__class__(self.__term, self.__formatting, *strings)

    def __new__(cls, term, formatting, *parts):
        rawstr = cls._render_parts(term, parts, formatting, use_formatting=False)
        self = str.__new__(cls, rawstr)
        self.__term = term
        self.__parts = parts
        self.__formatting = formatting
        return self

    def __add__(self, other):
        if isinstance(other, str):
            return self.__class__(self.__term, None, self, other)
        return NotImplemented

    @property
    def unformatted(self):
        """Explicitly access the unformatted string."""
        return super().__str__()

    def __str__(self):
        return self._render()

    def _render(self, **kw):
        return self._render_parts(
            self.__term, self.__parts, self.__formatting, **kw)

    @classmethod
    def _render_parts(cls, term, parts, formatting, reset=True,
                      use_formatting=True):
        rendered = []
        if use_formatting and formatting:
            rendered.append(formatting)
        for part in parts:
            if isinstance(part, BlessedString):
                if not use_formatting:
                    rendered.append(part.unformatted)
                else:
                    rendered.append(part._render(reset=False))
                    if formatting:
                        rendered.append(formatting)
            else:
                rendered.append(part)
        if reset and use_formatting:
            rendered.append(term.normal)
        return ''.join(rendered)


class BetterTerminal(blessings.Terminal):

    def string(self, formatting, parts):
        if isinstance(formatting, str) and not isinstance(
                formatting, blessings.FormattingString):
            formatting = getattr(self, formatting)
        return BlessedString(self, formatting, parts)


class ElasticString(str):
    """A string constructed from multiple parts, one of which can be
    "elastic". The string can be formatted to a maximum length, and the
    elastic part will be shortened to fit the requested length:

    ::

        str = ElasticString(
            before+' ',
            ElasticString.elastic(url),
            after
        )
        str.format(max_length=30)
    """

    class elastic(object):
        def __init__(self, str):
            self.str = str

    def __new__(cls, *parts):
        instance = str.__new__(cls, cls.format_parts(parts))
        instance.parts = parts
        return instance

    def __str__(self):
        return self.format(maxlength=None)

    def format(self, maxlength=None):
        return self.format_parts(self.parts, maxlength)

    @classmethod
    def shorten(self, s, length):
        if len(s) <= length:
            return s

        ellipsis = '...'
        chars_to_remove = (len(s) + len(ellipsis)) - length
        if chars_to_remove > len(s):
            return ellipsis[:length]

        middle = len(s)/2
        return s[:ceil(middle)-floor(chars_to_remove/2)] + \
               ellipsis + \
               s[ceil(middle)+ceil(chars_to_remove/2):]

    @classmethod
    def format_parts(cls, parts, maxlength=None):
        used_length = 0
        renderers = []
        elastic_part = None
        for part in parts:
            if isinstance(part, ElasticString.elastic):
                if maxlength and not elastic_part:
                    renderers.append(
                        (lambda p: lambda: cls.shorten(
                            p, maxlength-used_length))(part.str))
                    elastic_part = renderers[-1]
                else:
                    renderers.append((lambda p: lambda: p)(part.str))
            else:
                renderers.append((lambda p: lambda: p)(part))
                used_length += len(part)
        return ''.join([str(r()) for r in renderers])
