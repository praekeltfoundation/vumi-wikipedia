# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia_api -*-

import json
from urllib import urlencode

from twisted.internet.defer import inlineCallbacks, returnValue

from vumi.utils import http_request_full
from vumi import log

from .article_extract import ArticleExtract


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
    def _make_call(self, params):
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
        response = yield self._make_call({
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

        :returns: :class:`ArticleExtract` containing the article data.
        """
        response = yield self._make_call({
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
        returnValue(ArticleExtract(page['extract']))
