"""Implements the ability to store a set of urls locally.

This is actually one of the more challenging parts of writing a
spider. Here are my thoughts on the general issues while writing
this code. Careful: These do not describe the actual code.

---------------------------------------------------

Converting links:

    1) You want to convert (to local and full-host based urls) based
       on all other downloaded urls.

    2) You don't want this conversion to be dependant on a full
       spider run. They should be converted as new links are found,
       or have the ability to work for a continued copy (see below).

Working with an existing, possibly aborted mirror:

    3) You'd want to be able to use etags and the like to cut down
       on having to download all urls again.

    4) You'd want to be able to purge files from the mirror that have
       not been found in the new spider run.

Conclusions:

- A list of urls/files in the mirror needs to be kept and persisted.

  The alternative, reconstructing the urls from the local filenames is
  not feasable (see below).

  Having a list of urls in the mirror as opposed to a list of urls
  found during spidering allows purging (4).

  Metadata is kept alongside, which allows etag-based continuing (3).

- In fact, while working out the url form a filename is impossible,
  a mirror could be continued (3 and 4) without said database being
  available (say it has been deleted), albeit in a limited fashion.

  For (3), for each url processed the future file on disk could be
  checked, and the modified date used. Makes troubles with redirects.

  For (4), we could still purge all existing files *not* overwritten
  in the new spider run.

  This is probably not worth implementing though.

- The purging (4) can only work at the "when everything else is done"
  stage. It can't be done any earlier. We can never guarantee the spider
  might not come back to any url because the rules are so variable.

Replacing links:

- The list of processed urls/files is enough for converting the links at
  the end of the spider run, even a continued one (1, 2), by simply
  going through each file, parsing it, replacing the links.

- If we want to convert links more often (2), at the very least then
  the challenge is making this fast: parsing thousands of files over
  and over again is not an option.

  The first step is a mapping (url -> contained in file) so we'll
  only have to look at relevant files whenever a url is added.

  Generally, if this mapping goes missing, we can restore it by re-parsing
  all the files on disk.

  So to speed that up, we could have a mapping
  so we only need to parse those files that actually contain our urls.

  This means that when we continue (3), we'll have partially converted
  files on the disk. For files that are re-downloaded, we need to
  reset the (url -> contained in file) mapping.

- Further speed up options would be to store the parsed representation,
  possibly in the form of the positions of the urls in the file, so
  we can replace them without parsing.


Does the mirror need to parse HTML files?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes. Here is why.

We already parsed during download. However, for converting links, we need to
be able to replace a link in the first file when we download the last file.

When we have thousands of files, we can't keep these parsers in memory.

Serializing the result to disk is like use duplicate disk space, at least.

We can't even clear such a cache when a mirror is complete, because for
a mirror update, it is possible that a single updated page will yield a url
that requires updating te first page (due to the way rules work, the first
page may not have followed that url).

The only thing that would work: Keep a database of link positions and
lengths in files, update on every change. Pro: Fast; Con: Fiddly, if the
database is missing we need to re-download a page; if any changes are
made to the files by the user than this will break hard, and fixing it
would require elevating a failure within the Mirror class back to the Spider
to refetch the url.

However, the position-approach can be used as a speed up, using the parser
when it is not possible.

---------------------------------------------------

TODO: A option like "put in a special page for external missing links"
is an additional challenge, because we might also later have to replace
such a link.
TODO: Finally, during purging, we might have to do the reverse and
replace local urls with remote ones.
"""

import mimetypes
import os
from os import path
from urllib.parse import urlparse
import itertools
from track.parser import HTMLParser, get_parser_for_mimetype
from track.spider import get_content_type


def determine_filename(url, http_response):
    """Determine the filename under which to store a URL.
    """
    parsed = urlparse(url)
    # TODO: Query string

    # Prefix the domain to the filename
    filename = path.join(parsed.netloc, parsed.path[1:])

    # If we are dealing with a trailing-slash, create an index.html file
    # in a directory.
    if filename.endswith(path.sep):
        filename = path.join(filename, 'index.html')

    # If we are dealing with a file w/o an extension, add one
    if not path.splitext(filename)[1]:
        mime = get_content_type(http_response)
        # We need to get a list of all possible extensions and pick
        # on ourselves using sort(), since guess_extension() will
        # return a different one each time.
        extensions = mimetypes.guess_all_extensions(mime, strict=False)
        extensions.sort()
        if extensions:
            filename = '{0}{1}'.format(filename, extensions[0])

    # No more than 255 bytes per path segment, its rare a filesystem
    # supports more.
    filename = '/'.join(map(lambda s: s[:255], filename.split('/')))

    return filename


class Mirror(object):
    """Have local copy of one or multiple urls.
    """

    # maps urls in the mirror to the local filenames
    urls = {}
    # stores extra data like etags and mimetypes.
    url_info = {}
    # maps which urls are referenced by which other urls.
    url_usage = {}

    def __init__(self, directory, write_at_once=True, convert_links=True):
        self.directory = directory
        self.write_at_once = write_at_once
        self.convert_links = convert_links

    def open(self, filename, mode):
        """Open a file relative to the mirror directory.
        """
        full_filename = path.join(self.directory, filename)

        if not path.exists(path.dirname(full_filename)):
            os.makedirs(path.dirname(full_filename))
        return open(full_filename, mode)

    def add(self, response):
        """Store the given page.
        """
        # Store the file
        rel_filename = determine_filename(response.url, response)
        with self.open(rel_filename, 'wb') as f:
            f.write(response.content)

        # We also add a copy that will not be affected by any link
        # converting for debugging purposes. It'll allow us to validate
        # via a diff what the conversion is doing.
        if self.convert_links:
            with self.open(path.join('.originals', rel_filename), 'wb') as f:
                f.write(response.content)

        # Add to database
        self.urls[response.url] = rel_filename
        self.url_info[response.url] = {'mimetype': get_content_type(response)}
        for url, _ in response.parsed or ():
            self.url_usage.setdefault(url, [])
            self.url_usage[url].append(response.url)

        # See if we should apply modifications now (as opposed to waiting
        # until the last response has been added).
        if self.write_at_once:
            self._convert_links(response.url)
            self._create_index()

    def finish(self):
        self._convert_links()
        self._create_index()

    def _create_index(self):
        """Create an index file of all pages in the mirror.
        """
        result = ''
        for url, filename in self.urls.items():
            result += '<a href="{0}">{0}</a><br>'.format(filename)
        with self.open('index.html', 'w') as f:
            f.write(result)

    def _convert_links(self, for_url=None):
        """Convert links in all downloaded files, or all files
        that are known to link to ``for_url``.
        """
        if not self.convert_links:
            return

        if not for_url:
            files_to_process = self.urls.items()
        else:

            files_to_process = itertools.chain(
                # The url itself
                ((for_url, self.urls[for_url]),),
                # All files pointing to the url
                map(lambda u: (u, self.urls[u]),
                    self.url_usage.get(for_url, [])))
        for url, filename in files_to_process:
            self._convert_links_in_file(filename, url)

    def _convert_links_in_file(self, file, url):
        mimetype = self.url_info[url]['mimetype']
        parser_class = get_parser_for_mimetype(mimetype)
        if not parser_class:
            return

        with self.open(file, 'r+') as f:
            # A simple way to speed this up would also be to keep a
            # certain contingent of previously-parsed documents in memory.
            parsed = parser_class(f.read(), url)

            def replace_link(url):
                # We have a copy of this
                if url in self.urls:
                    target_filename = self.urls[url]
                    rel_link = path.relpath(target_filename, path.dirname(file))
                    return './{0}'.format(rel_link)

                else:
                    # We do not have a local copy. The url has already
                    # been absolutized by the parser, we can simply
                    # set it.
                    # We mustn't do this however for links that have
                    # already previously been replaced with a local
                    # link. We can find out if that is the case by
                    # checking our url usage database. If the url is not
                    # in it, then it must we one of ours.
                    # TODO: Not sure if this is fool-proof, or if we could
                    # in theory imagine a server-side link constructed in
                    # such a way that a match would occur here.
                    if url in self.url_usage:
                        return url
            new_content = parsed.replace_links(replace_link)

            # Write new file
            f.seek(0)
            f.write(new_content)
            f.truncate()

