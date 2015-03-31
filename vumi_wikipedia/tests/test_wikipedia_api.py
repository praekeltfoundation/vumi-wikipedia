import json
from functools import wraps
from pkg_resources import resource_stream

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.internet.protocol import Protocol, Factory
from twisted.trial.unittest import TestCase
from vumi.tests.helpers import VumiTestCase
from vumi.utils import HttpTimeoutError

from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract, APIError


class SectionMarkerCreator(object):
    def __getitem__(self, key):
        return u'\ufffd\ufffd%s\ufffd\ufffd' % (key,)


def make_extract(text, fullurl):
    return ArticleExtract(text % SectionMarkerCreator(), fullurl)


class ArticleExtractTestCase(TestCase):
    def assert_titles(self, ae, *titles):
        self.assertEqual(list(titles), [s.title for s in ae.sections])

    def assert_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.text for s in ae.sections])

    def assert_full_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.full_text() for s in ae.sections])

    def assert_section(self, section, title, text):
        self.assertEqual(title, section.title)
        self.assertEqual(text, section.text)

    def assert_fullurl(self, ae, url):
        self.assertEqual(ae.fullurl, url)

    def test_fullurl(self):
        url = 'http://en.wikipedia.org/wiki/foo'
        ae = make_extract(u'foo\nbar', url)
        self.assert_titles(ae, None)
        self.assert_texts(ae, u'foo\nbar')
        self.assert_fullurl(ae, url)

    def test_one_section(self):
        url = 'http://en.wikipedia.org/wiki/foo'
        ae = make_extract(u'foo\nbar', url)
        self.assert_titles(ae, None)
        self.assert_texts(ae, u'foo\nbar')

    def test_multiple_sections(self):
        url = 'http://en.wikipedia.org/wiki/foo'
        ae = make_extract(u'foo\n\n\n%(2)s bar \nbaz\n%(2)squux\n\n\nlol', url)
        self.assert_titles(ae, None, u'bar', u'quux')
        self.assert_texts(ae, u'foo', u'baz', u'lol')

    def test_shallow_nested_sections(self):
        url = 'http://en.wikipedia.org/wiki/foo'
        ae = make_extract(u'%(2)sfoo\n%(3)s bar \ntext\n%(3)s baz\nblah', url)
        self.assert_titles(ae, None, u'foo')
        self.assert_texts(ae, u'', u'')
        self.assert_full_texts(ae, u'', u'bar:\n\ntext\n\nbaz:\n\nblah')

        [s20, s21] = ae.sections[1].get_subsections()
        self.assert_section(s20, u'bar', u'text')
        self.assert_section(s21, u'baz', u'blah')

    def test_deep_nested_sections(self):
        url = 'http://en.wikipedia.org/wiki/foo'
        ae = make_extract('\n'.join([
                    u'%(2)ss1\nt1',
                    u'%(3)ss20\nt20',
                    u'%(3)ss21\nt21',
                    u'%(4)ss30\nt30',
                    u'%(4)ss31\nt31',
                    u'%(3)ss22\nt22',
                    ]), url)
        self.assert_titles(ae, None, u's1')
        self.assert_texts(ae, u'', u't1')
        self.assert_full_texts(ae, u'', '\n\n'.join([
                    u't1',
                    u's20:\n\nt20',
                    u's21:\n\nt21',
                    u's30:\n\nt30',
                    u's31:\n\nt31',
                    u's22:\n\nt22']))

        [intro, s1] = ae.sections
        [s20, s21, s22] = s1.get_subsections()
        [s30, s31] = s21.get_subsections()

        self.assertEqual([], intro.get_subsections())
        self.assertEqual([], s20.get_subsections())
        self.assertEqual([], s30.get_subsections())
        self.assertEqual([], s31.get_subsections())
        self.assertEqual([], s22.get_subsections())

        self.assert_section(intro, None, u'')
        self.assert_section(s1, u's1', u't1')
        self.assert_section(s20, u's20', u't20')
        self.assert_section(s21, u's21', u't21')
        self.assert_section(s30, u's30', u't30')
        self.assert_section(s31, u's31', u't31')
        self.assert_section(s22, u's22', u't22')

    def test_empty_input(self):
        ae = ArticleExtract(u'', '')
        self.assertEqual([u''], [s.text for s in ae.sections])
        self.assertEqual([None], [s.title for s in ae.sections])


WIKIPEDIA_RESPONSES = json.load(
    resource_stream(__name__, 'wikipedia_responses.json'))


def rewrite_request_line(request_line):
    """
    Sort the request parameters in the URL path so tests don't rely on
    deterministic dict ordering.
    """
    method, sp, url_path = request_line.partition(' ')
    path, q, params = url_path.partition('?')
    params = '&'.join(sorted(params.split('&')))
    url_path = q.join([path, params])
    return sp.join([method, url_path])


class FakeHTTP(Protocol):
    def dataReceived(self, data):
        request_line, body = self.parse_request(data)
        response = self.handle_request(request_line, body)
        self.transport.write(response.encode('utf-8'))
        self.transport.loseConnection()

    def parse_request(self, data):
        headers, _, body = data.partition('\r\n\r\n')
        headers = headers.splitlines()
        request_line = rewrite_request_line(headers.pop(0).rsplit(' ', 1)[0])
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
        resp_body = response_data["request_body"]
        if resp_body:
            resp_body = json.dumps(resp_body)
        self.factory.testcase.assertEqual(resp_body, body)
        return self.build_response(response_data)


class FakeHTTPTestCaseMixin(object):
    def _reformat_response_data(self, response_data):
        reformatted_response_data = {}
        for request_line, stuff in response_data.iteritems():
            request_line = rewrite_request_line(request_line)
            reformatted_response_data[request_line] = stuff
        return reformatted_response_data

    def start_webserver(self, response_data):
        factory = Factory()
        factory.protocol = FakeHTTP
        factory.response_data = self._reformat_response_data(response_data)
        factory.testcase = self
        webserver = reactor.listenTCP(0, factory, interface='127.0.0.1')
        self.add_cleanup(webserver.loseConnection)
        addr = webserver.getHost()
        webserver.url = "http://%s:%s/" % (addr.host, addr.port)
        return webserver


def debug_api_call(func):
    @wraps(func)
    def wrapped_test(self):
        self.wikipedia.PRINT_DEBUG = True
        self.wikipedia.url = self.wikipedia.URL
        return func(self)
    return wrapped_test


class WikipediaAPITestCase(VumiTestCase, FakeHTTPTestCaseMixin):
    def setUp(self):
        self.fake_api = self.start_webserver(WIKIPEDIA_RESPONSES)
        self.wikipedia = WikipediaAPI(self.fake_api.url, False)

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
    def test_search_custom_backend(self):
        yield self.assert_api_result(
            self.wikipedia.search('wikipedia', limit=3,
                                  backend='CirrusSearch'),
            [u'Wikipedia', u'Wikip\xe9dia', u'English Wikipedia'])

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
        self.wikipedia = WikipediaAPI(self.fake_api.url, False, 'Bob Howard')
        self.expected_user_agent = 'Bob Howard'
        yield self.wikipedia.get_extract('Cthulhu')

    def test_api_timeout(self):
        self.wikipedia = WikipediaAPI(self.fake_api.url, False, api_timeout=0)
        return self.assertFailure(
            self.wikipedia.get_extract('Cthulhu'), HttpTimeoutError)
