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
    u'It appears that on 1 March 1925, a thin, dark young man of ...\n(Full '
    u'content sent by SMS.)')

CTHULHU_SMS_NO_MORE = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark young man of neurotic and '
    u'excited aspect ...')

CTHULHU_SMS = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark young man of neurotic ... '
    u'(reply for more)')

CTHULHU_MORE = (
    u'...and excited aspect had called upon Professor Angell bearing the '
    u'singular clay bas-relief, which was then exceedingly damp and fresh. '
    u'... (reply for more)')

CTHULHU_END = (
    u'...anxious to preserve its conservatism, had found him quite hopeless. '
    u'(end of section)')


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
                'incoming_sms_transport': 'sphex_more',
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

    def assert_config_knob(self, attr, orig, new):
        self.assertEqual(orig, getattr(self.worker, attr))
        self.assertEqual(new, getattr(self.knobbly_worker, attr))

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
        self.assertEqual(CTHULHU_SMS_NO_MORE, sms_msg['content'])
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

    @inlineCallbacks
    def test_config_knobs(self):
        self.knobbly_worker = yield self.get_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'sms_transport': 'sphex_sms',

                'api_url': 'https://localhost:1337/',
                'accept_gzip': True,
                'user_agent': 'Bob Howard',
                'max_ussd_session_length': 200,
                'content_cache_time': 1800,
                'max_ussd_content_length': 180,
                'max_ussd_unicode_length': 80,
                'max_sms_content_length': 300,
                'max_sms_unicode_length': 130,
                })

        self.assert_config_knob('api_url', self.url, 'https://localhost:1337/')
        self.assert_config_knob('accept_gzip', None, True)
        self.assert_config_knob('user_agent', None, 'Bob Howard')
        self.assert_config_knob('max_ussd_session_length', 180, 200)
        self.assert_config_knob('content_cache_time', 3600, 1800)
        self.assert_config_knob('max_ussd_content_length', 160, 180)
        self.assert_config_knob('max_ussd_unicode_length', 70, 80)
        self.assert_config_knob('max_sms_content_length', 160, 300)
        self.assert_config_knob('max_sms_unicode_length', 70, 130)

    @inlineCallbacks
    def test_happy_flow_more(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        for _ in range(8):
            yield self.dispatch(self.mkmsg_in('more'), 'sphex_more.inbound')

        sms = self._amqp.get_messages('vumi', 'sphex_sms.outbound')
        self.assertEqual(CTHULHU_SMS, sms[0]['content'])
        self.assertEqual('+41791234567', sms[0]['to_addr'])

        self.assertEqual(CTHULHU_MORE, sms[1]['content'])
        self.assertEqual('+41791234567', sms[1]['to_addr'])

        self.assertEqual(CTHULHU_END, sms[-1]['content'])
        self.assertEqual('+41791234567', sms[-1]['to_addr'])

    @inlineCallbacks
    def test_more_then_new(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        yield self.dispatch(self.mkmsg_in('more'), 'sphex_more.inbound')

        [sms_0, sms_1] = self._amqp.get_messages('vumi', 'sphex_sms.outbound')
        self.assertEqual(CTHULHU_SMS, sms_0['content'])
        self.assertEqual('+41791234567', sms_0['to_addr'])
        self.assertEqual(CTHULHU_MORE, sms_1['content'])
        self.assertEqual('+41791234567', sms_1['to_addr'])

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
