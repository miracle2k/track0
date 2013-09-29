import mimetypes
import os
from os import path
from urllib.parse import urlparse
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
        extension = mimetypes.guess_extension(mime, strict=False)
        if extension:
            filename = '{0}{1}'.format(filename, extension)

    # No more than 255 bytes per path segment, its rare a filesystem
    # supports more.
    filename = '/'.join(map(lambda s: s[:255], filename.split('/')))

    return filename


class Mirror(object):
    """Represents a local copy of one or multiple urls.
    """

    def __init__(self, directory, write_at_once=True):
        self.directory = directory
        self.write_at_once = write_at_once

        self._pages = []

    def open(self, filename, mode):
        """Open a file relative to the mirror directory.
        """
        full_filename = path.join(self.directory, filename)

        if not path.exists(path.dirname(full_filename)):
            os.makedirs(path.dirname(full_filename))
        return open(full_filename, mode)

    def add(self, page):
        """Store the given page.
        """
        rel_filename = determine_filename(page.url, page)
        with self.open(rel_filename, 'w') as f:
            f.write(page.text)

        page.filename = rel_filename
        self._pages.append(page)

        if self.write_at_once:
            self.create_index()

    def finish(self):
        self.create_index()

    def create_index(self):
        """Create an index file of all pages in the mirror.
        """
        result = ''
        for page in self._pages:
            result += '<a href="{0}">{0}</a><br>'.format(page.filename)
        with self.open('index.html', 'w') as f:
            f.write(result)


