from track.cli import tests, CLIRules
from textwrap import dedent, wrap


def main():
    print(dedent('''
    ===============
    Available Tests
    ===============

    '''))

    for key in tests.AvailableTests:
        if not key:
            continue
        test = CLIRules.get_test(key)
        docstring = test.__doc__.strip()
        if not docstring.startswith(' '):
            # Guess what the right indentation is.
            docstring = '    '*2+docstring

        s = dedent("""
        {name}
        {hr}

        {desc}
        """).format(name=key, desc=dedent(docstring), hr='-'*len(key))
        print(dedent(s))


main()
