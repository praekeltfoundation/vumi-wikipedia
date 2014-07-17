# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia_api -*-

import re
import json
from urllib import urlencode

from twisted.internet.defer import inlineCallbacks, returnValue

from vumi.utils import http_request_full
from vumi import log


def either(*args):
    for arg in args:
        if arg is not None:
            return arg
    return None


ARTICLE_SPLITTER = re.compile(u'\ufffd\ufffd(?=\\d\ufffd\ufffd)')
ARTICLE_SECTION = re.compile(
    u'^(\\d)\ufffd\ufffd\\s*([^\\n]+?)\\s*(?:|\\n+(.*))$', re.DOTALL)


class APIError(Exception):
    """
    Exception thrown by Wikipedia API.
    """


class ArticleExtract(object):
    """
    Class representing an article extract
    """

    def __init__(self, data, fullurl=''):
        if isinstance(data, dict):
            #rebuild AritcleExtract from session
            self.sections = [ArticleSection.from_dict(section)
                             for section in data['sections']]
            self.fullurl = data.get('fullurl', '')
        elif isinstance(data, list):
            #rebuild ArticleExtract for legacy vumi cached data
            self.sections = data
            self.fullurl = fullurl
        else:
            #create ArticleExtract from raw wiki data
            self.fullurl = fullurl
            self._from_string(data)

    def _from_string(self, data):
        split_data = ARTICLE_SPLITTER.split(data)

        # Start building the section tree with the intro.
        self.sections = [ArticleSection(0, None, split_data[0].strip())]

        section_bits = []
        for section in split_data[1:]:
            section = section.strip()
            m = ARTICLE_SECTION.match(section)
            level, title, text = m.groups()
            level = int(level)
            if text is None:
                text = u''
            section_bits.append((level, title, text))

        # Not all section levels are used, so we renumber them for consistency.
        section_levels = list(sorted(set(s[0] for s in section_bits)))
        levels = dict((l, i) for i, l in enumerate(section_levels))

        for level, title, text in section_bits:
            level = levels[level]
            section = ArticleSection(level, title, text)
            if level == 0:
                self.sections.append(section)
            else:
                self.sections[-1].add_subsection(section)

    def to_json(self):
        return json.dumps({
            'fullurl': self.fullurl,
            'sections': [s.to_dict() for s in self.sections]
        })

    @classmethod
    def from_json(cls, data):
        return cls(json.loads(data))


class ArticleSection(object):
    def __init__(self, level, title, text):
        self.level = level
        self.title = title
        self.text = text
        self._subsections = []

    def add_subsection(self, subsection):
        if subsection.level > self.level + 1:
            if self._subsections:
                self._subsections[-1].add_subsection(subsection)
                return
        self._subsections.append(subsection)

    def get_subsections(self):
        # Return a shallow copy to avoid accidental mutation.
        return self._subsections[:]

    def __repr__(self):
        return '<%s: %r (%s)>' % (type(self).__name__, self.title, self.level)

    def full_text(self):
        text = self.text
        for section in self.get_subsections():
            if text:
                text += '\n\n'
            text += '%s:\n\n%s' % (section.title, section.full_text())
        return text

    def to_dict(self):
        return {
            'level': self.level,
            'title': self.title,
            'text': self.text,
            'sections': [s.to_dict() for s in self.get_subsections()],
            }

    @classmethod
    def from_dict(cls, data):
        section_extract = cls(data['level'], data['title'], data['text'])
        for subsection in data['sections']:
            section_extract.add_subsection(cls.from_dict(subsection))
        return section_extract


class WikipediaAPI(object):
    """
    Small Wikipedia API client library.

    :param str url: URL of the API to query. (Defaults to Wikipedia's API.)
    :param bool gzip: `True` to ask for gzip encoding, `False` otherwise.
    """

    URL = 'http://en.wikipedia.org/w/api.php'

    # The MediaWiki API docs request that clients use gzip encoding to reduce
    # network traffic. However, Twisted only supports this easily from 11.1.
    GZIP = False

    USER_AGENT = 'Vumi HTTP Request'

    PRINT_DEBUG = False

    def __init__(self, url=None, gzip=None, user_agent=None, api_timeout=None):
        self.url = either(url, self.URL)
        self.gzip = either(gzip, self.GZIP)
        self.user_agent = either(user_agent, self.USER_AGENT)
        self.api_timeout = api_timeout

    @inlineCallbacks
    def _make_call(self, params):
        params.setdefault('format', 'json')
        url = '%s?%s' % (self.url, urlencode(params))
        if isinstance(url, unicode):
            url = url.encode('utf-8')
        headers = {'User-Agent': self.user_agent}
        if self.gzip:
            headers['Accept-Encoding'] = 'gzip'
        if self.PRINT_DEBUG:
            print "\n=====\n\n%s /?%s\n" % ('GET', url.split('?', 1)[1])
        response = yield http_request_full(
            url, '', headers, method='GET', timeout=self.api_timeout)
        if self.PRINT_DEBUG:
            print response.delivered_body
            print "\n====="
        try:
            returnValue(json.loads(response.delivered_body))
        except Exception, e:
            log.msg("Error reading API response: %s %r" % (
                    response.code, response.delivered_body))
            log.err()
            raise APIError(e)

    @inlineCallbacks
    def search(self, query, limit=9, backend=None):
        """
        Perform a query and returns a list of results matching the query.

        :param unicode query: Search terms.
        :param int limit: Maximum number of results to return. (Default 9)
        :param unicode backend: The backend to use. Defaults to whatever
            Wikimedia uses. See
            http://en.wikipedia.org/w/api.php?action=help&modules=query+search
            for list of available backends.

        :returns: `list` of article titles matching search terms.
        """
        params = {
            'action': 'query',
            'list': 'search',
            'srsearch': query.encode('utf-8'),
            'srlimit': str(limit),
        }
        if backend is not None:
            params['srbackend'] = backend

        response = yield self._make_call(params)
        if 'query' not in response:
            raise APIError(response)
        results = [r['title']
                   for r in response['query'].get('search', {})]
        returnValue(results)

    @inlineCallbacks
    def get_extract(self, page_name):
        """
        Return the content of a section of a page.

        :param unicode page_name: The name of the page to query.

        :returns: :class:`ArticleExtract` containing the article data.
        """
        response = yield self._make_call({
                'action': 'query',
                'prop': 'extracts|info',
                'inprop': 'url',
                'explaintext': '',
                'exsectionformat': 'raw',
                'titles': page_name.encode('utf-8'),
                'redirects': '1',
            })
        if 'query' not in response:
            raise APIError(response)
        _id, page = response['query']['pages'].popitem()
        returnValue(ArticleExtract(page['extract'], page['fullurl']))
