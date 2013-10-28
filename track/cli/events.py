from collections import Counter
from requests.exceptions import ConnectionError
from track.spider import Events
from track.utils import NoneDict
from .utils import BetterTerminal, ElasticString


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
        error_msg = ''
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
                if isinstance(follow_state['exception'], ConnectionError):
                    error_msg = 'connection refused'
                elif link.response.status_code:
                    result = str(link.response.status_code)
                    error_msg = link.response.reason
            elif follow_state['failed'] == 'not-modified':
                pass
            else:
                url_style = error
        if not result:
            result = '   '
            status_style = error

        if error_msg:
            error_msg = '[{}] '.format(error_msg)

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
            t.string(error, error_msg),
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
