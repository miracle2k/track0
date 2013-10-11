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
import hashlib
import shelve
from urllib.parse import urlparse
import itertools
from track.parser import get_parser_for_mimetype
from track.spider import get_content_type, Link


def safe_filename(filename):
    # No more than 255 bytes per path segment, its rare a filesystem
    # supports more.
    return '/'.join(map(lambda s: s[:255], filename.split('/')))


class Mirror(object):
    """Have local copy of one or multiple urls.
    """

    @classmethod
    def is_valid_mirror(cls, directory):
        """Check if the directory contains a track mirror."""
        if not path.exists(directory):
            return False
        if not path.exists(path.join(directory, '.track')):
            return False
        return True

    def __init__(self, directory, write_at_once=True, convert_links=True):
        self.directory = directory
        self.write_at_once = write_at_once
        self.convert_links = convert_links

        # All urls stored in the mirror.
        # .. is persisted so we know what is in the currently stored in
        #    the mirror (vs. what the spider has found this time around).
        # .. we could look at the filesystem itself for this, but would
        #    run the risk of deleting files that do not belong to us.
        self.stored_urls = self.open_shelve('urls')
        # stores extra data like etags and mimetypes
        self.url_info = self.open_shelve('url_info')
        # used to store arbitrary extra data
        self.info = self.open_shelve('info')

        # a separate map that essentially marks urls as "encountered",
        # for use with the :meth:`delete_unencountered` method.
        self.encountered_urls = {}
        # the redirects that we know about
        # TODO: Think about whether we need to separate redirects into
        # encountered and stored in the same way we handle uls.
        self.redirects = {}

        # Generate a maps which provide for each url a list of pages
        # that point to said url.
        self.url_usage = {}
        for url, data in self.url_info.items():
            self._insert_into_url_usage(url, data['links'])

    def get_filename(self, link, response):
        """Determine the filename under which to store a URL.

        This is designed for subclasses to be able to provide a custom
        implementation. They do not need to care about making the
        filename safe for the filesystem.
        """
        link = response.url

        parsed = urlparse(link)

        # Prefix the domain to the filename
        filename = path.join(parsed.netloc, parsed.path[1:])

        # If we are dealing with a trailing-slash, create an index file
        # in a directory - extension added later.
        if filename.endswith(path.sep):
            filename = path.join(filename, 'index')

        # If we are dealing with a file w/o an extension, add one
        if not path.splitext(filename)[1]:
            mime = get_content_type(response)
            # We need to get a list of all possible extensions and pick
            # on ourselves using sort(), since guess_extension() will
            # return a different one each time.
            extensions = mimetypes.guess_all_extensions(mime, strict=False)
            extensions.sort()
            if extensions:
                filename = '{0}{1}'.format(filename, extensions[0])

        # If there is a query string, insert it before the extension
        if parsed.query:
            base, ext = path.splitext(filename)
            hash = hashlib.md5(parsed.query.encode()).hexdigest().lower()[:10]
            filename = '{}_{}{}'.format(base, hash, ext)

        return filename

    def open(self, filename, mode):
        """Open a file relative to the mirror directory.
        """
        full_filename = path.join(self.directory, filename)

        if not path.exists(path.dirname(full_filename)):
            os.makedirs(path.dirname(full_filename))
        return open(full_filename, mode)

    def open_shelve(self, filename):
        """Open a persistent dictionary.
        """
        track_dir = path.join(self.directory, '.track')
        if not path.exists(track_dir):
            os.makedirs(track_dir)

        return shelve.open(path.join(track_dir, filename))

    def add(self, link, response):
        """Store the given page.
        """
        # Figure out the filename first
        rel_filename = self.get_filename(link, response)
        # TODO: Make sure the filename is not outside the cache directory,
        # avoid issues with servers injecting special path instructions.
        rel_filename = safe_filename(rel_filename)
        # Do not allow writing inside the data directory, this would
        # possibly allow code injection
        assert not rel_filename.startswith('.track/')

        # Store the file
        with self.open(rel_filename, 'wb') as f:
            f.write(response.content)

        # We also add a copy that will not be affected by any link
        # converting for debugging purposes. It'll allow us to validate
        # via a diff what the conversion is doing.
        if self.convert_links:
            with self.open(path.join('.originals', rel_filename), 'wb') as f:
                f.write(response.content)

        # Add to database: data about the url
        url_info = {
            'mimetype': get_content_type(response),
            'etag': response.headers.get('etag'),
            'last-modified': response.headers.get('last-modified'),
            'links': []
        }
        for url, info in itertools.chain(
                response.links_parsed,
                response.parsed or ()):
            url_info['links'].append((url, info))
        self.url_info[response.url] = url_info
        # The url itself
        self.encountered_urls[response.url] = rel_filename
        self.stored_urls.setdefault(response.url, set())
        self.stored_urls[response.url] |= {rel_filename}
        # Be sure to to update the reverse cache
        self._insert_into_url_usage(link.url, url_info['links'])
        # Make sure database is saved
        self.flush()

        # See if we should apply modifications now (as opposed to waiting
        # until the last response has been added).
        if self.write_at_once:
            self._convert_links(response.url)
            self._create_index()

    def encounter_url(self, link):
        """Add a url to the list of encountered urls.

        This is like add(), except it doesn't actually save anything. It
        will protect this url from being deleted by
        :meth:`delete_unencountered`.
        """
        url = link.url
        assert url in self.stored_urls
        # When storing the same url using a different mirror layout without
        # using delete_unregistred() to get rid of the old one, it is
        # possible to end up with a single url being stored multiple times.
        # In such a case, we just one at random to keep. This may are may
        # not be the one that matches the current layout the user wants.
        # TODO: This points to a related and deeper problem: the users
        # desired filename may not be among the stored list, if due to
        # a 304 response the file does not get written again using a new
        # mirror filename layout. Part of the problem is that in order
        # to generate the correct filename, we need a response object here;
        # the 304 status response may suffice.
        self.encountered_urls[url] = list(self.stored_urls[url])[0]

    def add_redirect(self, link, target_link, code):
        """Register a redirect.

        Will make sure that any links pointing to ``url`` can be rewritten
        to the file behind ``target_url``.
        """
        self.redirects[link.url] = (code, target_link.url)
        self.flush()

    def finish(self):
        self._convert_links()
        self._create_index()
        self.flush()

    def flush(self):
        """Write the internal mirror data structures to disk.
        """
        self.stored_urls.sync()
        self.url_info.sync()
        self.info.sync()

    def _insert_into_url_usage(self, url, links):
        for link, info in links:
            self.url_usage.setdefault(link, set())
            self.url_usage[link] |= {url}

    def _create_index(self):
        """Create an index file of all pages in the mirror.
        """
        result = ''
        for url, filenames in self.stored_urls.items():
            result += '<a href="{0}">{0}</a><br>'.format(list(filenames)[0])
        with self.open('index.html', 'w') as f:
            f.write(result)

    def _convert_links(self, for_url=None):
        """Convert links in all downloaded files, or all files
        that are known to link to ``for_url``.
        """
        if not self.convert_links:
            return

        # We only handle links between urls encountered in this run, not
        # all urls in the mirror. I.e. using the same mirror for multiple
        # runs with different urls will not link between them.
        # The reason is that we cannot combine write_at_once mode with
        # :meth:`delete_unrefreshed`. We'd first be rewriting a link to
        # an existing file, then that file gets deleted afterwards.
        #
        # Possible solutions:
        #
        #   - Only link between all urls if deletion is not used.
        #     Problem: API design issues (--enable-delete is a CLI option),
        #        inconsistent behaviour hard to explain.
        #
        #   - Find a way to write these replaced links back to the original
        #     once a file gets deleted. If link replacing worked with indices
        #     rather than a fresh parsing that would be simple.
        #
        # XXX: Actually, the problem affects us even without write_at_once;
        # it's a general issue with the deletion: It's possible that a file
        # gets deleted during a mirror-update, but a different file that
        # was linking to it was not re-downloaded due to 304, therefore
        # retaining the local link. What we really need is a way to write
        # local links back to their original ones, and this should not be
        # so hard given that we have a database of urls->filenames.
        url_database = self.encountered_urls

        if not for_url:
            files_to_process = url_database.items()
        else:
            files_to_process = itertools.chain(
                # The url itself
                ((for_url, url_database[for_url]),),
                # All files pointing to the url
                [(u, url_database[u])
                 for u in self.url_usage.get(for_url, [])
                 if u in url_database])
        for url, filename in files_to_process:
            self._convert_links_in_file(filename, url, url_database)

    def _convert_links_in_file(self, file, url, url_database):
        mimetype = self.url_info[url]['mimetype']
        parser_class = get_parser_for_mimetype(mimetype)
        if not parser_class:
            return

        with self.open(file, 'r+') as f:
            # A simple way to speed this up would also be to keep a
            # certain contingent of previously-parsed documents in memory.
            parsed = parser_class(f.read(), url)

            def replace_link(url):
                # Abuse the URL class to normalize the url for matching
                link = Link(url)
                url = link.url

                # See what we know about this link. Is the target url
                # saved locally? Is it a known redirect?
                local_filename = redir_url = redir_code = None
                if url in url_database:
                    local_filename = url_database[url]
                else:
                    if url in self.redirects:
                        redir_code, redir_url = self.redirects[url]
                        if redir_url in url_database:
                            local_filename = url_database[redir_url]

                # We have the document behind this link available locally
                if local_filename:
                    rel_link = path.relpath(local_filename, path.dirname(file))
                    if link.lossy_url_data.get('fragment'):
                        rel_link += '#' + link.lossy_url_data['fragment']
                    return './{0}'.format(rel_link)

                # It is a permanent redirect, use the redirect target
                elif redir_url and redir_code == 301:
                    return redir_url

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

    def delete_unencountered(self):
        """This will delete all files in the mirror that have not
        been explicitly registered with this instance.

        In other words, if an existing mirror on the filesystem is
        opened that already has urls in it, they will all be deleted
        unless :meth:`add` has been called for them.
        """
        for url in self.stored_urls:
            files_to_delete = []

            if not url in self.encountered_urls:
                # Remove the url entirely
                files_to_delete = self.stored_urls[url]
                del self.stored_urls[url]
                del self.url_info[url]

            elif self.stored_urls[url] != self.encountered_urls.get(url):
                # The local save path of the url has changed. Remove all
                # previous local files that used to belong to the url.
                files_to_delete = self.stored_urls[url] - {self.encountered_urls[url]}
                # Update the stored
                self.stored_urls[url] = {self.encountered_urls[url]}

            for name in files_to_delete:
                print('deleting', name)
                filename = path.join(self.directory, name)
                os.unlink(filename)
                clear_directory_structure(filename)

        # Regenerate url link cache
        self.url_usage = {}
        for url, data in self.url_info.items():
            self._insert_into_url_usage(url, data['links'])


def clear_directory_structure(filename):
    """Delete an empty directory structure from where the place
    where ``filename`` used to be located.
    """
    directory = path.dirname(filename)
    while os.listdir(directory) == []:
        os.rmdir(directory)
        directory = path.dirname(directory)


