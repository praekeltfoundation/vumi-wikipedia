# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia_api -*-

import re
import json
from urllib import urlencode


from bs4 import BeautifulSoup, element
from twisted.internet.defer import inlineCallbacks, returnValue

from vumi.utils import http_request_full
from vumi import log


def either(*args):
    for arg in args:
        if arg is not None:
            return arg
    return None


class APIError(Exception):
    """
    Exception thrown by Wikipedia API.
    """


class WikipediaAPI(object):
    """
    Small Wikipedia API client library.

    :param str url: URL of the API to query. (Defaults to Wikipedia's API.)
    :param bool gzip: `True` to ask for gzip encoding, `False` otherwise.
    """

    URL = 'http://en.wikipedia.org/w/api.php'

    PARSOID_URL = 'http://parsoid.wmflabs.org/en'

    # The MediaWiki API docs request that clients use gzip encoding to reduce
    # network traffic. However, Twisted only supports this easily from 11.1.
    GZIP = False

    USER_AGENT = 'Vumi HTTP Request'

    PRINT_DEBUG = False

    def __init__(self, url=None, gzip=None, user_agent=None):
        self.url = either(url, self.URL)
        self.gzip = either(gzip, self.GZIP)
        self.user_agent = either(user_agent, self.USER_AGENT)

    @inlineCallbacks
    def _make_call(self, url):
        headers = {'User-Agent': self.user_agent}
        if self.gzip:
            headers['Accept-Encoding'] = 'gzip'
        if self.PRINT_DEBUG:
            if '?' in url:
                print "\n=====\n\n%s /?%s\n" % ('GET', url.split('?', 1)[1])
            else:
                # print "\n=====\n\n%s /%s\n" % ('GET', url.rsplit('/', 1)[1])
                print "\n=====\n\n%s /%s\n" % ('GET', url.rsplit('/', 1)[1])
        response = yield http_request_full(
            url.encode('utf8'), '', headers, method='GET')
        if self.PRINT_DEBUG:
            print response.delivered_body
            print "\n====="
        returnValue((response.code, response.delivered_body))

    @inlineCallbacks
    def _make_api_call(self, params):
        params.setdefault('format', 'json')
        url = '%s?%s' % (self.url, urlencode(params))
        code, body = yield self._make_call(url)
        try:
            returnValue(json.loads(body))
        except Exception, e:
            log.msg("Error reading API response: %s %r" % (code, body))
            log.err()
            raise APIError(e)

    @inlineCallbacks
    def _make_parsoid_call(self, params):
        params.setdefault('format', 'json')
        url = '%s?%s' % (self.url, urlencode(params))
        headers = {'User-Agent': self.user_agent}
        if self.gzip:
            headers['Accept-Encoding'] = 'gzip'
        if self.PRINT_DEBUG:
            print "\n=====\n\n%s /?%s\n" % ('GET', url.split('?', 1)[1])
        response = yield http_request_full(url, '', headers, method='GET')
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
    def search(self, query, limit=9):
        """
        Perform a query and returns a list of results matching the query.

        :param unicode query: Search terms.
        :param int limit: Maximum number of results to return. (Default 9)

        :returns: `list` of article titles matching search terms.
        """
        response = yield self._make_api_call({
                'action': 'query',
                'list': 'search',
                'srsearch': query.encode('utf-8'),
                'srlimit': str(limit),
                })
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

        :returns: :class:`WikipediaArticle` containing the article data.
        """
        response = yield self._make_api_call({
                'action': 'query',
                'prop': 'extracts',
                'explaintext': '',
                'exsectionformat': 'raw',
                'titles': page_name.encode('utf-8'),
                'redirects': '1',
                })
        if 'query' not in response:
            raise APIError(response)
        _id, page = response['query']['pages'].popitem()
        returnValue(article_from_extract(page['extract']))

    @inlineCallbacks
    def get_parsoid(self, page_name):
        """
        Return the Parsoid-generated content of a page.

        :param unicode page_name: The name of the page to query.

        :returns: :class:`ParsoidArticle` containing the article data.
        """
        url = '%s/%s' % (self.PARSOID_URL, page_name)
        _code, body = yield self._make_call(url)
        returnValue(article_from_parsoid(body))


class WikipediaArticle(object):
    """Class representing a Wikipedia article."""

    def __init__(self, sections):
        if isinstance(sections[0], ArticleSection):
            # We already have a set of sections, so just assign it.
            self.sections = sections
        else:
            self._from_tuples(sections)

    def _from_tuples(self, sections):
        # First section is special.
        self.sections = [ArticleSection(*sections.pop(0))]

        # Not all section levels are used, so we renumber them for consistency.
        section_levels = list(sorted(set(s[0] for s in sections)))
        levels = dict((l, i) for i, l in enumerate(section_levels))

        for level, title, text in sections:
            level = levels[level]
            section = ArticleSection(level, title, text)
            if level == 0:
                self.sections.append(section)
            else:
                self.sections[-1].add_subsection(section)

    def to_json(self):
        return json.dumps([s.to_dict() for s in self.sections])

    @classmethod
    def from_json(cls, data):
        return cls([ArticleSection.from_dict(section)
                    for section in json.loads(data)])


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


ARTICLE_SPLITTER = re.compile(u'\ufffd\ufffd(?=\\d\ufffd\ufffd)')
ARTICLE_SECTION = re.compile(
    u'^(\\d)\ufffd\ufffd\\s*([^\\n]+?)\\s*(?:|\\n+(.*))$', re.DOTALL)


def article_from_extract(data):
    split_data = ARTICLE_SPLITTER.split(data)

    # Start building the section tree with the intro.
    sections = [(0, None, split_data[0].strip())]

    for section in split_data[1:]:
        section = section.strip()
        m = ARTICLE_SECTION.match(section)
        level, title, text = m.groups()
        level = int(level)
        if text is None:
            text = u''
        sections.append((level, title, text))

    return WikipediaArticle(sections)


H_RE = re.compile('^h([0-9]+)$')


def article_from_parsoid(data):
    soup = BeautifulSoup(data)

    sections = []

    level = 0
    title = None
    content = []
    for el in soup.html.body:
        if isinstance(el, element.Tag):
            match = H_RE.match(el.name)
            if match:
                sections.append((level, title, ''.join(content).strip()))
                level = int(match.group(1))
                title = el.get_text()
                content = []
            else:
                content.append(el.get_text())
        else:
            content.append(el)
    sections.append((level, title, ''.join(content).strip()))

    return WikipediaArticle(sections)
