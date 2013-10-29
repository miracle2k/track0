=====================================
A web spider that makes sense (to me)
=====================================

.. parsed-literal::

    $ track http://en.wikipedia.org/
        **@follow**
            +original-domain

Mirrors all of Wikipedia.


.. parsed-literal::

    $ track http://en.wikipedia.org/
        **@follow**
            +original-domain
            +domain=en.wiktionary.org,fr.wiktionary.org

Mirrors all of Wikipedia, and also follow links to the English and French
Wiktionaries.


.. parsed-literal::

    $ track http://en.wikipedia.org/
        **@follow**
            +domain=\*.wikipedia.org
            -domain=en.wikipedia.org


Mirrors all copies of Wikipedia, except the English one.


.. parsed-literal::

    $ track http://en.wikipedia.org/
        **@follow**
            +original-domain
        **@save**
            +path=P\*

Mirrors all Wikipedia pages starting with a ``P``, but crawls all of
Wikipedia to find them.


.. parsed-literal::

    $ track http://commons.wikimedia.org/
        **@follow**
            +original-domain
        **@save**
            +type=image
            -size>1M

Downloads all images files from Wikipedia Commons, but no single file
larger than 1 megabyte.


How it works:
-------------

One or more initial urls are added to a queue. This queue is processed
until it is exhausted.

For each url, the ``@follow`` rules are run. If they do not pass, the
url is ignored and the spider moves on to the next url.

If it passes, the url is fetched and it's links are added to the queue.

Then, the ``@save`` rules are checked to see whether it should be
saved to the local mirror.

The rules are a series of tests. If a test passes, the url is either
allowed (if the test is prefixed with a ``+``) or disallowed (if the
test is prefixed with a ``-``). The tests are processed from left to right,
meaning later passing tests overwrite earlier passing tests. This is
quite important to understand::

    -depth>3 +original-domain

The above is likely a mistake: All urls on the starting domain
would be followed to an indefinite depth, whereas the first test has
no effect at all, because unless a ``+`` test matches, a url is already
skipped by default. Turn this around and it makes more sense::

    +original-domain -depth>3

Now a url, by default marked as "skipped", will be allowed if it is on
the same domain as the source - unless it was found after more than three
steps, because the depth test is the final step all urls have to pass.

There are simple yes/no tests (``original-domain``), tests that match
text (``domain=google.com``) and tests that compare numbers
(``depth<3``). See the full list of `available tests`_.

.. _available tests: https://github.com/miracle2k/track0/wiki/Available-Tests


Installation
------------

Python 3.3 is required on your machine::

    $ sudo easy_install3 track0


Why?
----

This was born out of my frustration trying to make HTTrack work for me.
I never understood how all the different options were expected to interact,
and I decided there must be a better way. So this is my vision as to how
a website copier could work.

Could work, because while this works reasonably well already, its missing
a bunch of important features, and is severely undertested. At this point,
you should consider it more of a proof of concept.

Among the things currently not supported:

    - Only a single request at a time, no concurrent connections.
    - robots.txt is ignored.
    - No support for authentication, HTTP or cookie-based.
    - No fancy (or any) JavaScript parsing.

Why not wget?
~~~~~~~~~~~~~

wget can recursive download and correct local links accordingly, does
the job quite well, and has a good API. However, it doesn't quite go
beyond the downloading to full mirroring: for example, updating an
existing mirror isn't really something it is designed for (*). Similarly,
as it lacks the concept of a local mirror, different staring urls are
not aware of each other.

(*) There is limited support for checking the timestamps of existing
files, but only if it's links have not been adjusted, or backup copies
exist.



More documentation
------------------

::

    $ track http://en.wikipedia.org/wiki/Pushing_Daisies

The above command line, where no rules are specified, will download the
given page and all of the files required to display it offline (images,
stylesheets, etc.), but will not follow any links to further pages. It
will create a folder ``./tracked`` in the working directory for this.

It is therefore equivalent to::

    $ track
        http://en.wikipedia.org/wiki/Pushing_Daisies
        -O tracked
        @follow - +requisite
        @save +


Since the ``@save`` rule default is ``+``, it usually suffices that you
set up a ``@follow``, unless you are interested in only saving a subset
of the files encountered.


Requisites
~~~~~~~~~~

Requisites deserve further mention. Generally, track does not differentiate
between different types of files. Whether the url being processed points to
an HTML page or an image file, it will apply the rules in the same way (the
only difference is that an image file cannot point to any further urls).

Because it is a common use case to want to mirror a page in such a way that
it can be locally viewed without accessing to the original server, and
because web pages are a collection of a multitude of different files
(images, scripts, stylesheets and more), track has been written to have some
knowledge about which files are required to display a page. These urls are
internally flagged as *requisites*. By using the rule ``@follow +requisite``,
you are ensuring that all such urls are followed.

The requisite test is quite smart. It will only match the requisites of
pages that are actually saved. Take for example the following::

    $ track http://politics.stackexchange.com/
        @follow +original-domain +requisite
        @save +path=*fiscal* +requisite

This would spider the whole site, but only save pages where the path
contains the word ``fiscal``.


Link conversion
~~~~~~~~~~~~~~~

By default, the local mirror will be modified so that all links are
working: If a file is available locally, the url will be modified to
refer to the local copy. Otherwise, the url will be modified so that
it refers to the original copy using a full domain name.

It is possible to turn this behaviour off using the
``-no-link-conversion`` switch.


Update an existing mirror
~~~~~~~~~~~~~~~~~~~~~~~~~

Inside the mirror will be a hidden folder containing the data that track
needs to update a mirror, including things like etags and last-modified
dates which are used to avoid re-downloading content where possible.

To update a mirror, simple call track while with the correct directory::

    $ track -O ./local-mirror

The mirror knows what arguments where used the last time, and will use them
again for the update.

You can happily use the same directory for multiple different sites::

    $ track -O ./local-mirror http://requests.readthedocs.org/
    $ track -O ./local-mirror http://lwn.net/

Note however that only the arguments of the last call are remembered. So
in the above case, if you update the mirror with a simple
``track -O ./local-mirror``, only ``http://lwn.net`` is repeated.

By default, track only ever adds or changes files in the local mirror; it
never deletes any existing pages. You can change this behaviour::

    $ track -O ./local-mirror --enable-delete

Using this flag, all existing files that where not encountered and saved
during this run will be deleted afterwards. This doesn't work well with
dumping multiple sites into the same directory though, as described above.

    .. note::
        The delete mode does not mean "delete pages that no longer exist
        online"; it means: "delete pages not encountered by the spider
        tis time". For example, imagine you have mirrored a site like this::

             $ track http://example.org @follow "+depth<=3"

        Then, you update it with a modified follow rule::

             $ track --enable-delete http://example.org @follow "+depth<=2"

        This means that all pages on depth level 3 will be removed.



Breaking tests
~~~~~~~~~~~~~~

In addition to the `+` and `-` rules that you are already familiar with,
you can also use ``++`` or ``--``. Those mean: if the test matches, stop
the rule evaluation right here, with the respective result.

For example::

    $ track http://en.wikipedia.org/
        @follow ++original-domain
                +domain=en.wiktionary.org
                -domain-depth>0

This would mirror all of Wikipedia. Only links that go to a different
domain than ``en.wikipedia.org`` pass the first test. Those that go
to the English Wikionary will be allowed, but must also pass the last
test, which ensures that they are not followed any further: Only the
initial Wiktionary page will be mirrored.


The stop rule
~~~~~~~~~~~~~

In addition to ``@follow`` and ``@save``, you can also define a ``@stop``
rule. This is rarely needed. If the rule matches a url, no links from
that url will be followed.

The key is that it runs after ``@save``, while ``@follow`` runs before.


Sites that require login
~~~~~~~~~~~~~~~~~~~~~~~~

.. note::
    This is a work in progress.

HTTP Auth is not yet supported. Cookie-based auth can be done by simulating
a POST request:

    track -O local-mirror --cookies persist http://example.org/login{user=foo,password=bar} @save -


Redirects
~~~~~~~~~

If a url redirects to a different location, the redirect target needs to
pass the ``@follow`` rule. That is in addition to the url that does the
redirecting, which needs to pass at least those tests that run before the
redirect is detected.

For example, a ``+original-domain`` test needs to pass both urls. A
``+size>100k`` test only needs to pass the target url: Clearly, it wouldn't
make much sense to require the redirect itself to be large. The same thing
is true for tests like ``content`` or ``content-type``.

The local copy in the mirror will always be saved under a filename
representing the target url.

.. note::
    If there is more than a single redirect in a chain, only the final url
    needs to pass the rules: For example, if you filter by domain, presumably
    you will not be bothered if a redirect takes a round trip through a
    different domain; its the final document that matters.

track also deals with a special case where a url is known to be a redirect,
but is not saved to the local mirror, presumably because the ``@save``
rule did not match. If the url was using a permanent redirect with status
code ``301``, links to that url will be replaced with a link to the target
location instead.

Let's look at a example. Say a page has as a link like this::

    http://feedproxy.google.com/~rFooBar/~3/2fdgmfhHu1k/

Redirecting, using a 301 permanent redirect, to the real address::

    http://example.org/blog-entry.html

If you have configured the spider to not follow urls to ``example.org``,
the local mirror will still rewrite links to point directly to
``http://example.org``.

In a different case, you might have a url like this::

    http://example.org/download.php?file=foobar

using a temporary redirect to::

    http://example.org/data/foobar.zip

In this case, the local mirror will contain the link to the ``download.php``
file; the download generator will remain intact, rather than linking to
the internal file.


Understanding the output
------------------------

.. raw:: html

    <style type="text/css">
      ol.p1 {
        padding: 10px;
        background-color: black; color: white; font: 14.0px 'Courier New';
        list-style-type: none;
        counter-reset: sig-lines;
        margin-left: 0px;
      }
      ol.p1 li { position: relative; margin-bottom: 2px; }
      ol.p1 li.sig:before {
        content: counter(sig-lines);
        counter-increment: sig-lines;
        position: absolute;
        left: -35px;
        background-color: black;
        border-radius: 50%;
        padding: 5px;
        top: -2px;
        font-size: 12px;
        display: inline-block;
        width: 12px; height: 12px;
        text-align: center;
      }
      span.s1 {color: #33bd26}
      span.s2 {font: 14.0px Menlo; color: #33bd26}
      span.s3 {color: #2de71f}
      span.s4 {color: #afae24}
      span.s5 {color: #c33720}
      span.s6 {color: #edec15}
      span.f {white-space: pre-wrap;}
    </style>
    <ol class="p1">
    <li>$ track0 https://github.com/jay/wget/ @follow +requisite +original-domain @save "+size&lt;100k”</li>
    <li class="sig"><span class="s2 f"> ⚑ </span> <b>https://github.com/jay/wget/</b> <b>+162</b>/170 <span class="s3">@+size&lt;100k</span></li>
    <li class="sig"><span class="s2 f"> ⚑ </span> <b>https://github.com/opensearch.xml</b> <span class="s3">@+original-domain @+size&lt;100k</span></li>
    <li class="sig"><span class="s1 f">304</span> <b>https://github.com/apple-touch-icon-144.png</b> <span class="s3">@+original-domain</span></li>
    <li class="sig"><span class="s4 f"> - </span> https://github-media-downloads.s3.amazonaws.com/github-logo.svg</li>
    <li><span class="s4 f"> - </span> https://github.global.ssl.fastly.net/</li>
    <li><span class="s4 f"> - </span> https://ghconduit.com:25035/</li>
    <li class="sig"><span class="s5 f">404 [Not Found] https://github.com/_sockets </span><span class="s3">@+original-domain</span></li>
    <li><span class="s2 f"> ⚑ </span> <b>https://github.global.ssl.fastly.net/assets/github-9b546507dc975fac304850c6b7b906a4b5889df9.css</b> <b>+33</b>/33 <span class="s3">@+requisite @+size&lt;100k</span></li>
    <li class="sig"><span class="s1 f"> → </span> https://github.com/features <span class="s3">@+original-domain</span></li>
    <li><span class="s2 f"> ⚑ </span> <b>https://github.com/features/projects</b> <b>+57</b>/77 <span class="s3">@+original-domain @+size&lt;100k</span></li>
    <li class="sig"><span class="s1 f"> → </span> https://github.com/jay/wget/archive/master.zip <span class="s3">@+original-domain</span></li>
    <li><span class="s4 f"> - </span> https://codeload.github.com/jay/wget/zip/master</li>
    <li class="sig"><span class="s1 f"> + </span> <span class="s6">https://github.com/jay/wget/commits/master </span><b>+240</b>/280 <span class="s3">@+original-domain</span></li>
    <li>  [2059 queued, 43 files saved]</li>
    </ol>


1. The url was saved (⚑, bold text).
   It contains 170 links, 162 of which where added to the queue. The
   missing links were unsupported types like ftp urls, or often
   ``javascript:`` links.
   Since it was a starting url provided by the user, it did not need to
   pass any tests to be followed, and it was saved because it matched
   the size test (``@+size<100k``).

2. This url was followed because it matched the ``+original-domain`` test,
   and it was saved because it matched the save rule (``@+size<100k``).
   The first test listed is the determinant ``@follow`` rule, the second one
   from the determinant ``@save`` rule. If you have not specified a
   ``@save`` rule, it will not be given.

3. The ``304`` indicates that this file already exists in the local mirror,
   and the server informed us that it has not changed. It was not downloaded
   again.

4. The following three urls were skipped (-), because no ``@follow`` rule
   matched. They were neither recognized as page requisites, nor are they
   on the same domain as the starting url.

5. The url could not be downloaded. In this case, the server returned a
   ``404 Not Found`` status.

6. This is an example of a redirect (→). Both the redirect itself and the
   target url pass the ``@follow`` rule, and the file is saved.

7. This is another redirect, except in this case, you can see that the
   second url has not been followed

8. This url was followed (+). However, it did not pass the ``@save``
   rules. The line is missing the save indicator (⚑, bold text), and
   is instead colored yellow. You can see that despite not being saved,
   it contains 240 links that will be followed.

Other recipes
-------------

Saving all images from a site
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    $ track
        http://en.wikipedia.org
        --layout {url|md5|10}_{filename}
        @follow +original-domain
        @save +content-type=image/*


Grab the first page from any external site
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    $ track
        http://bookmarks.com/
        @follow +original-domain +domain-depth=0

This uses the ``domain-depth`` test, which is the depth since the spider
arrived at the current domain. Therefore, the rule above would spider the
original domain, but would also allow any urls that were just discovered
pointing to a different domain.


Allowing a size range
~~~~~~~~~~~~~~~~~~~~~

This would be the standard way::

    $ track
        http://www.example.org
        @follow +size>10 -size>20

But just for fun, here are some other options::

    + -size<10 -size>20
    - --size>20 +size>10

