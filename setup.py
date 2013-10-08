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
        'beautifulsoup4',
        'requests',
        'urlnorm>=1.1.2'
    ],
    test_requires=[
        'pytest',
        'requests-testadapter'
    ],
    entry_points="""[console_scripts]\ntrack = track.cli:run\n"""
)

