"""Tests for vumi.demos.wikipedia."""

from twisted.internet.defer import inlineCallbacks

from vumi.application.tests.test_base import ApplicationTestCase

from vumi_wikipedia.wikipedia import WikipediaWorker
from vumi_wikipedia.tests.test_wikipedia_api import (
    FakeHTTPTestCaseMixin, WIKIPEDIA_RESPONSES)


CTHULHU_RESULTS = '\n'.join([
        u'1. Cthulhu',
        u'2. Call of Cthulhu (role-playing game)',
        u'3. Cthulhu (2007 film)',
        u'4. Cthulhu (2000 film)',
        u'5. Cthulhu Mythos',
        u'6. The Call of Cthulhu',
        ])

CTHULHU_SECTIONS = '\n'.join([
        u'1. Cthulhu',
        u'2. History',
        u'3. Geography',
        u'4. Mountains of Madness',
        u'5. Lulz',
        ])

CTHULHU_USSD = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark young man of...\n(Full '
    u'content sent by SMS.)')

CTHULHU_SMS = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark young man of neurotic '
    u'and excited aspect had...')


class WikipediaWorkerTestCase(ApplicationTestCase, FakeHTTPTestCaseMixin):
    application_class = WikipediaWorker

    @inlineCallbacks
    def setUp(self):
        yield super(WikipediaWorkerTestCase, self).setUp()
        yield self.start_webserver(WIKIPEDIA_RESPONSES)
        self.worker = yield self.get_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'sms_transport': 'sphex_sms',
                'api_url': self.url,
                })
        self.wikipedia = self.worker.wikipedia

    @inlineCallbacks
    def replace_application(self, config):
        # Replace our worker with a different one.
        self._workers.remove(self.worker)
        yield self.worker.stopWorker()
        self.worker = yield self.get_application(config)

    @inlineCallbacks
    def assert_response(self, text, expected):
        yield self.dispatch(self.mkmsg_in(text))
        self.assertEqual(expected,
                         self.get_dispatched_messages()[-1]['content'])

    def start_session(self):
        return self.assert_response(
            None, 'What would you like to search Wikipedia for?')

    @inlineCallbacks
    def tearDown(self):
        yield self.stop_webserver()
        yield super(WikipediaWorkerTestCase, self).tearDown()

    @inlineCallbacks
    def search_for_content(self, search, result=1, section=1):
        yield self.dispatch(self.mkmsg_in(None))  # Start session.
        yield self.dispatch(self.mkmsg_in(search))  # Search keyword.
        yield self.dispatch(self.mkmsg_in(str(result)))  # Select result.
        yield self.dispatch(self.mkmsg_in(str(section)))  # Select section.

    def test_make_options(self):
        self.assertEqual((2, "1. foo\n2. bar"),
                         self.worker.make_options(['foo', 'bar']))

    @inlineCallbacks
    def test_happy_flow(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self._amqp.get_messages('vumi', 'sphex_sms.outbound')
        self.assertEqual(CTHULHU_SMS, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])

    @inlineCallbacks
    def test_no_sms_transport(self):
        yield self.replace_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'api_url': self.url,
                })

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        self.assertEqual(
            [], self._amqp.get_messages('vumi', 'sphex_sms.outbound'))

    @inlineCallbacks
    def test_sms_override(self):
        yield self.replace_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'sms_transport': 'sphex_sms',
                'api_url': self.url,
                'override_sms_address': 'blah',
                })

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self._amqp.get_messages('vumi', 'sphex_sms.outbound')
        self.assertEqual(CTHULHU_SMS, sms_msg['content'])
        self.assertEqual('blah', sms_msg['to_addr'])

    @inlineCallbacks
    def test_invalid_selection_not_digit(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response(
            'six', 'Sorry, invalid selection. Please restart and try again')

    @inlineCallbacks
    def test_invalid_selection_bad_index(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response(
            '8', 'Sorry, invalid selection. Please restart and try again')

    @inlineCallbacks
    def test_invalid_selection_later(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response(
            'Hastur', 'Sorry, invalid selection. Please restart and try again')

    @inlineCallbacks
    def test_search_no_results(self):
        yield self.start_session()
        yield self.assert_response(
            'ncdkiuagdqpowebjkcs',
            'Sorry, no Wikipedia results for ncdkiuagdqpowebjkcs')

    @inlineCallbacks
    def test_search_error(self):
        yield self.start_session()
        yield self.assert_response(
            '.', ('Sorry, there was an error processing your request. Please '
                  'try ' 'again later.'))
        self.flushLoggedErrors()
