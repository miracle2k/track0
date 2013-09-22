import argparse
import sys
from track.mirror import Mirror
from track.spider import Spider, Rules


class RuleImpl(object):

    @staticmethod
    def everywhere(url, value):
        return True

    @staticmethod
    def domain(url, value):
        return url.parsed.netloc == url.root.parsed.netloc

    @staticmethod
    def depth(self, url, value):
        return url.depth <= int(value)



AvailableRules = {
    'everywhere': RuleImpl,
    'domain': RuleImpl,
}


class CLIRules(Rules):
    """Makes the spider follow the rules defined in the argparse
    namespace given.
    """

    def __init__(self, arguments):
        self.arguments = arguments
        self.follow_rules = list(map(
            lambda f: (f.split('=', 1) + [None])[:2], arguments.follow))

    def follow(self, url):
        for name, value in self.follow_rules:
            rule = AvailableRules[name]
            if not getattr(rule, name)(url, value):
                return False
        return True

    def save(self, url):
        return True



def main(argv):
    parser = argparse.ArgumentParser(argv[0])
    parser.add_argument('url', nargs='+')
    parser.add_argument('--follow', nargs='+', metavar= 'RULE')

    namespace = parser.parse_args(argv[1:])
    spider = Spider(CLIRules(namespace), mirror=Mirror('/tmp/test'))
    for url in namespace.url:
        spider.add(url)
    spider.loop()


def run():
    sys.exit(main(sys.argv) or 0)
