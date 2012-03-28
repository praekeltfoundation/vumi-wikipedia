# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia_api -*-
from twisted.internet.defer import inlineCallbacks, returnValue
import json
from urllib import urlencode
from vumi.utils import http_request_full
from vumi_wikipedia.text_manglers import (
    mangle_text, convert_unicode, normalize_whitespace, strip_html)
import re
import time
import pprint

def either(*args):
    for arg in args:
        if arg is not None:
            return arg
    return None

def section_marker(title=u'(\\d)'):
    return u'\ufffd\ufffd' + unicode(title) + u'\ufffd\ufffd'

class ArticleExtract(object):
    """
    Class representing an article extract
    """

    def __init__(self, data):
        if isinstance(data, unicode):
            self._init_from_string(data)
        else:
            self.sections = data

    def _init_from_string(self, string):
        splitter = re.compile(u'\ufffd\ufffd(?=\d)')
        do_section = re.compile(u'^(\\d)\ufffd\ufffd\s*([^\n]+?)\s*(?:|\n+(.*))$', re.DOTALL)
        self.sections = []
        for section in splitter.split(string):
            section = section.strip()
            if len(self.sections) > 0:
                m = do_section.match(section)
                level, title, text = m.groups()
                level = int(level)
                if text == None:
                    text = u''
            else:
                title, text, level = ( None, section, None )
            self.sections.append({'title': title, 'level': level, 'text': text})

    def get_section_titles(self):
        return [section['title'] for section in self.sections[1:]]

    def get_section_texts(self):
        return [section['text'] for section in self.sections]

    def get_top_level_sections(self):
        level = 10
        result = []
        for section in self.sections:
            if section['level'] == None or section['level'] <= level:
                result.append(section)
            if section['level'] != None:
                level = min(level, section['level'])
        return result
                

class WikipediaAPI(object):
    """
    Small Wikipedia API client library.
    """

    URL = 'http://en.wikipedia.org/w/api.php'

    # The MediaWiki API docs request that clients use gzip encoding to reduce
    # network traffic. However, Twisted only supports this easily from 11.1.
    GZIP = False

    PRINT_DEBUG = False

    def __init__(self, url=None, gzip=None):
        self.url = either(url, self.URL)
        self.gzip = either(gzip, self.GZIP)

    @inlineCallbacks
    def _make_call(self, params):
        params.setdefault('format', 'json')
        url = '%s?%s' % (self.url, urlencode(params))
        headers = {
            'User-Agent': 'Vumi HTTP Request',
            }
        if self.gzip:
            headers['Accept-Encoding'] = 'gzip'
        if self.PRINT_DEBUG:
            print "\n=====\n\n%s /?%s\n" % ('GET', url.split('?', 1)[1])
        response = yield http_request_full(url, '', headers, method='GET')
        if self.PRINT_DEBUG:
            print response.delivered_body
            print "\n====="
        returnValue(json.loads(response.delivered_body))

    @inlineCallbacks
    def search(self, query, limit=9):
        """
        Perform a query and returns a list of results matching the query.

        Parameters
        ----------
        query : unicode
            The search term.
        limit : int, optional
            How many results to get back, defaults to 9.
        """
        response = yield self._make_call({
                'action': 'query',
                'list': 'search',
                'srsearch': query.encode('utf-8'),
                'srlimit': str(limit),
                })
        results = [r['title'] for r in response['query']['search']]
        returnValue(results)

    @inlineCallbacks
    def get_extract(self, page_name):
        """
        Return the content of a section of a page.

        Parameters
        ----------
        page_name : unicode
            The name of the page to query.
        """
        response = yield self._make_call({
                'action': 'query',
                'prop': 'extracts',
                'explaintext': '',
                'exsectionformat': 'raw',
                'titles': page_name.encode('utf-8'),
                'redirects': '1',
                })
        id,page = response['query']['pages'].popitem()
        returnValue(ArticleExtract(page['extract']))

