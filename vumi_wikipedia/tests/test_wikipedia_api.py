import json
import time
from functools import wraps
from pkg_resources import resource_stream

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.protocol import Protocol, Factory
from twisted.trial.unittest import TestCase
from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract, section_marker
import pprint

class ArticleExtractTestCase(TestCase):
    def test_one_section(self):
        ae = ArticleExtract(u'foo\nbar')
        self.assertEqual([u'foo\nbar'], ae.get_section_texts())
        self.assertEqual([], ae.get_section_titles())
        self.assertEqual([{'title': None, 'level': None, 'text': u'foo\nbar'}], ae.sections)
        self.assertEqual([{'title': None, 'level': None, 'text': u'foo\nbar'}],
            ae.get_top_level_sections())
    
    def test_multiple_sections(self):
        ae = ArticleExtract(u'foo\n\n\n' + section_marker(2) + u' bar \nbaz\n' 
            + section_marker(2) + u'quux\n\n\nlol')
        self.assertEqual([u'foo', u'baz', u'lol'], ae.get_section_texts())
        self.assertEqual([u'bar', u'quux'], ae.get_section_titles())
        
    def test_nested_sections(self):
        ae = ArticleExtract(section_marker(2) + u'foo\n' + section_marker(3) + u' bar \ntext')
        self.assertEqual([u'', u'', u'text'], ae.get_section_texts())
        self.assertEqual([u'foo', u'bar'], ae.get_section_titles())
        self.assertEqual([{'level': None, 'text': u'', 'title': None},
            {'level': 2, 'text': u'', 'title': u'foo'}
            ], ae.get_top_level_sections())

    def test_empty_input(self):
        ae = ArticleExtract(u'')
        self.assertEqual([u''], ae.get_section_texts())
        self.assertEqual([], ae.get_section_titles())
        self.assertEqual([{'title': None, 'level': None, 'text': u''}], ae.sections)


WIKIPEDIA_RESPONSES = json.load(
    resource_stream(__name__, 'wikipedia_responses.json'))


class FakeHTTP(Protocol):
    def dataReceived(self, data):
        request_line, body = self.parse_request(data)
        response = self.handle_request(request_line, body)
        self.transport.write(response.encode('utf-8'))
        self.transport.loseConnection()

    def parse_request(self, data):
        headers, _, body = data.partition('\r\n\r\n')
        headers = headers.splitlines()
        request_line = headers.pop(0).rsplit(' ', 1)[0]
        return request_line, body

    def build_response(self, response_data):
        lines = ["HTTP/1.1 %s" % (response_data['response_code'],)]
        lines.extend(['', json.dumps(response_data['response_body'])])
        return '\r\n'.join(lines)

    def handle_request(self, request_line, body):
        response_data = self.factory.response_data.get(request_line)
        if not response_data:
            self.factory.testcase.fail(
                "Unexpected request: %s" % (request_line,))
        self.factory.testcase.assertEqual(response_data["request_body"], body)
        return self.build_response(response_data)


class FakeHTTPTestCaseMixin(object):
    @inlineCallbacks
    def start_webserver(self, response_data):
        factory = Factory()
        factory.protocol = FakeHTTP
        factory.response_data = response_data
        factory.testcase = self
        self.webserver = yield reactor.listenTCP(0, factory)
        addr = self.webserver.getHost()
        self.url = "http://%s:%s/" % (addr.host, addr.port)

    def stop_webserver(self):
        return self.webserver.loseConnection()


def debug_api_call(func):
    @wraps(func)
    def wrapped_test(self):
        self.wikipedia.PRINT_DEBUG = True
        self.wikipedia.url = self.wikipedia.URL
        return func(self)
    return wrapped_test


class WikipediaAPITestCase(TestCase, FakeHTTPTestCaseMixin):
    timeout = 10

    @inlineCallbacks
    def setUp(self):
        yield self.start_webserver(WIKIPEDIA_RESPONSES)
        self.wikipedia = WikipediaAPI(self.url, False)

    def tearDown(self):
        return self.stop_webserver()

    def assert_api_result(self, api_result_d, expected):
        return api_result_d.addCallback(self.assertEqual, expected)

    @inlineCallbacks
    def test_search_success(self):
        yield self.assert_api_result(
            self.wikipedia.search('wikipedia', limit=3),
            [u'Wikipedia', u'Wikip\xe9dia', u'Main Page'])
        # And again with a different request and result limit
        yield self.assert_api_result(
            self.wikipedia.search('vumi', limit=2),
            [u'Arambagh Utsab', u'Vulpia microstachys'])

    @inlineCallbacks
    def test_search_no_results(self):
        yield self.assert_api_result(
            self.wikipedia.search('ncdkiuagdqpowebjkcs', limit=3), [])
    
    @inlineCallbacks
    def test_get_extract(self):
        yield self.wikipedia.get_extract('Cthulhu').addCallback(self.assert_extract)
        
    def assert_extract(self, extract):
        self.assertEqual(5, len(extract.sections))

