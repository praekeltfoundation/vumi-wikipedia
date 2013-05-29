import json
from functools import wraps
from pkg_resources import resource_stream

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.internet.protocol import Protocol, Factory
from twisted.trial.unittest import TestCase

from vumi_wikipedia.wikipedia_api import (
    WikipediaAPI, APIError, article_from_extract, article_from_parsoid)


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


class SectionMarkerCreator(object):
    def __getitem__(self, key):
        return u'\ufffd\ufffd%s\ufffd\ufffd' % (key,)


class ArticleFromExtractTestCase(TestCase):
    def extract(self, text):
        return article_from_extract(text % SectionMarkerCreator())

    def assert_titles(self, ae, *titles):
        self.assertEqual(list(titles), [s.title for s in ae.sections])

    def assert_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.text for s in ae.sections])

    def assert_full_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.full_text() for s in ae.sections])

    def assert_section(self, section, title, text):
        self.assertEqual(title, section.title)
        self.assertEqual(text, section.text)

    def test_one_section(self):
        ae = self.extract(u'foo\nbar')
        self.assert_titles(ae, None)
        self.assert_texts(ae, u'foo\nbar')

    def test_multiple_sections(self):
        ae = self.extract(u'foo\n\n\n%(2)s bar \nbaz\n%(2)squux\n\n\nlol')
        self.assert_titles(ae, None, u'bar', u'quux')
        self.assert_texts(ae, u'foo', u'baz', u'lol')

    def test_shallow_nested_sections(self):
        ae = self.extract(u'%(2)sfoo\n%(3)s bar \ntext\n%(3)s baz\nblah')
        self.assert_titles(ae, None, u'foo')
        self.assert_texts(ae, u'', u'')
        self.assert_full_texts(ae, u'', u'bar:\n\ntext\n\nbaz:\n\nblah')

        [s20, s21] = ae.sections[1].get_subsections()
        self.assert_section(s20, u'bar', u'text')
        self.assert_section(s21, u'baz', u'blah')

    def test_deep_nested_sections(self):
        ae = self.extract('\n'.join([
                    u'%(2)ss1\nt1',
                    u'%(3)ss20\nt20',
                    u'%(3)ss21\nt21',
                    u'%(4)ss30\nt30',
                    u'%(4)ss31\nt31',
                    u'%(3)ss22\nt22',
                    ]))
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
        ae = article_from_extract(u'')
        self.assertEqual([u''], [s.text for s in ae.sections])
        self.assertEqual([None], [s.title for s in ae.sections])


def pt(tag, content, attrs=(), close=True):
    has_dp = False
    bits = [tag]
    for attr in attrs:
        if attr.startswith('data-parsoid='):
            has_dp = True
        bits.append(attr)
    if not has_dp:
        bits.append('data-parsoid=\'{"dsr":[0,0,null,null]}\'')

    if content is None:
        cl = ' /' if close else ''
        return '<%s%s>' % (' '.join(bits), cl)
    else:
        return '<%s>%s</%s>' % (' '.join(bits), content, tag)


def ptp(text):
    return '\n\n' + pt('p', text)


def pth(num, title):
    return '\n\n' + pt('h%s' % num, title)


class ArticleFromParsoidTestCase(TestCase):
    def extract(self, title, first_section, sections):
        header = (
            '<!DOCTYPE html>\n'
            '<html data-parsoid="{}" prefix="mw: http://mediawiki.org/rdf/">'
            '<head data-parsoid="{}" prefix="schema: http://schema.org/">'
            '<meta charset="UTF-8">'
            '<meta property="mw:articleNamespace" content="0">'
            '<meta property="schema:CreativeWork/version" content="555959481">'
            '<meta property="schema:CreativeWork/comment"'
            ' content="/* Parser */ determininistic">'
            '<title>%s</title>'
            '<base href="//en.wikipedia.org/wiki/%s">'
            '</head><body data-parsoid=\'xxx\'>') % (title, title)
        footer = '</body></html'

        html = ''.join([
            header,
            first_section,
            ''.join([''.join([pth(num, name), content])
                     for num, name, content in sections]),
            footer])
        # print "====="
        # print html
        # print "====="

        return article_from_parsoid(html)

    def assert_titles(self, ae, *titles):
        self.assertEqual(list(titles), [s.title for s in ae.sections])

    def assert_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.text for s in ae.sections])

    def assert_full_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.full_text() for s in ae.sections])

    def assert_section(self, section, title, text):
        self.assertEqual(title, section.title)
        self.assertEqual(text, section.text)

    def test_one_section(self):
        ae = self.extract(u'Title', ptp(u'foo') + ptp(u'bar'), [])
        self.assert_titles(ae, None)
        self.assert_texts(ae, u'foo\n\nbar')

    def test_multiple_sections(self):
        ae = self.extract(u'Title', ptp(u'foo'), [
            (2, 'bar', ptp(u'baz')),
            (2, 'quux', ptp(u'lol'))])
        self.assert_titles(ae, None, u'bar', u'quux')
        self.assert_texts(ae, u'foo', u'baz', u'lol')

    def test_shallow_nested_sections(self):
        ae = self.extract(u'Title', '', [
            (2, 'foo', ''),
            (3, 'bar', ptp('text')),
            (3, 'baz', ptp('blah'))])
        self.assert_titles(ae, None, u'foo')
        self.assert_texts(ae, u'', u'')
        self.assert_full_texts(ae, u'', u'bar:\n\ntext\n\nbaz:\n\nblah')

        [s20, s21] = ae.sections[1].get_subsections()
        self.assert_section(s20, u'bar', u'text')
        self.assert_section(s21, u'baz', u'blah')

    def test_deep_nested_sections(self):
        ae = self.extract(u'Title', '', [
            (2, 's1', ptp('t1')),
            (3, 's20', ptp('t20')),
            (3, 's21', ptp('t21')),
            (4, 's30', ptp('t30')),
            (4, 's31', ptp('t31')),
            (3, 's22', ptp('t22'))])
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
        ae = article_from_parsoid(u'')
        self.assertEqual([u''], [s.text for s in ae.sections])
        self.assertEqual([None], [s.title for s in ae.sections])
