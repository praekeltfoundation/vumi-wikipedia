import json
from functools import wraps
from pkg_resources import resource_stream

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.internet.protocol import Protocol, Factory
from twisted.trial.unittest import TestCase

from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract, APIError


class SectionMarkerCreator(object):
    def __getitem__(self, key):
        return u'\ufffd\ufffd%s\ufffd\ufffd' % (key,)


def make_extract(text):
    return ArticleExtract(text % SectionMarkerCreator())


class ArticleExtractTestCase(TestCase):
    def assert_titles(self, ae, *titles):
        self.assertEqual(list(titles), [s.title for s in ae.sections])

    def assert_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.text for s in ae.sections])

    def assert_full_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.full_text() for s in ae.sections])

    def test_one_section(self):
        ae = make_extract(u'foo\nbar')
        self.assert_titles(ae, None)
        self.assert_texts(ae, u'foo\nbar')

    def test_multiple_sections(self):
        ae = make_extract(u'foo\n\n\n%(2)s bar \nbaz\n%(2)squux\n\n\nlol')
        self.assert_titles(ae, None, u'bar', u'quux')
        self.assert_texts(ae, u'foo', u'baz', u'lol')

    def test_nested_sections(self):
        ae = make_extract(u'%(2)sfoo\n%(3)s bar \ntext')
        self.assert_titles(ae, None, u'foo')
        self.assert_texts(ae, u'', u'')
        self.assert_full_texts(ae, u'', u'bar:\n\ntext')

        self.assertEqual(u'bar', ae.sections[1].get_subsections()[0].title)
        self.assertEqual(u'text', ae.sections[1].get_subsections()[0].text)

    def test_empty_input(self):
        ae = ArticleExtract(u'')
        self.assertEqual([u''], [s.text for s in ae.sections])
        self.assertEqual([None], [s.title for s in ae.sections])


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
        self.assert_user_agent(headers)
        return request_line, body

    def assert_user_agent(self, headers):
        expected_user_agent = getattr(
            self.factory.testcase, 'expected_user_agent', None)
        if expected_user_agent is not None:
            [user_agent] = [h.split(': ', 1)[1] for h in headers
                            if h.lower().startswith('user-agent')]
            self.factory.testcase.assertEqual(expected_user_agent, user_agent)

    def build_response(self, response_data):
        lines = ["HTTP/1.1 %s" % (response_data['response_code'],)]
        body = response_data['response_body']
        if isinstance(body, dict):
            body = json.dumps(body)
        lines.extend(['', body])
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

    def test_search_error(self):
        return self.assertFailure(self.wikipedia.search('.'), APIError)

    @inlineCallbacks
    def test_bad_response(self):
        yield self.assertFailure(self.wikipedia.search('notjson'), APIError)
        self.flushLoggedErrors()

    def test_search_no_results(self):
        return self.assert_api_result(
            self.wikipedia.search('ncdkiuagdqpowebjkcs'), [])

    def test_get_extract(self):
        def assert_extract(extract):
            self.assertEqual(4, len(extract.sections))

        return self.wikipedia.get_extract('Cthulhu').addCallback(
            assert_extract)

    @inlineCallbacks
    def test_user_agent(self):
        self.expected_user_agent = self.wikipedia.USER_AGENT
        yield self.wikipedia.get_extract('Cthulhu')
        self.wikipedia = WikipediaAPI(self.url, False, 'Bob Howard')
        self.expected_user_agent = 'Bob Howard'
        yield self.wikipedia.get_extract('Cthulhu')
