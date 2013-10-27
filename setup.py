#!/usr/bin/env python3
# encoding: utf8

from setuptools import setup


# Figure out the version
import track
version = '.'.join(map(lambda s: str(s), track.__version__))


setup(
    name='track',
    version=version,
    description="A website copier.",
    classifiers=[
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Topic :: Internet :: WWW/HTTP'
    ],
    author='Michael Elsdörfer',
    author_email='michael@elsdoerfer.com',
    url='http://github.com/miracle2k/track',
    license='BSD',
    packages=['track'],
    install_requires=[
        'requests>=2.0.1',
        'urlnorm==custom,<99999',
        'charade',
        'html5lib',
        'reppy==custom,<99999',
        'blessings==1.5.1'
    ],
    test_requires=[
        'pytest',
        'requests-testadapter'
    ],
    dependency_links=[
        'https://github.com/miracle2k/urlnorm/archive/python3.zip#egg=urlnorm-custom',
        'https://github.com/miracle2k/reppy/archive/0eeb95.zip#egg=reppy-custom',
    ],
    entry_points="""[console_scripts]\ntrack0 = track.cli:run\n""",
)

