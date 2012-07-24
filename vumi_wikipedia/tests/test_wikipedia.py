"""Tests for vumi.demos.wikipedia."""

from twisted.internet.defer import inlineCallbacks

from vumi.application.tests.test_base import ApplicationTestCase

from vumi_wikipedia.wikipedia import WikipediaWorker
from vumi_wikipedia.tests.test_wikipedia_api import (
    FakeHTTPTestCaseMixin, WIKIPEDIA_RESPONSES)


class WikipediaWorkerTestCase(ApplicationTestCase, FakeHTTPTestCaseMixin):
    application_class = WikipediaWorker

    @inlineCallbacks
    def setUp(self):
        yield super(WikipediaWorkerTestCase, self).setUp()
        yield self.start_webserver(WIKIPEDIA_RESPONSES)
        self.worker = yield self.get_wikipedia_worker()
        self.wikipedia = self.worker.wikipedia

    @inlineCallbacks
    def tearDown(self):
        yield self.stop_webserver()
        yield super(WikipediaWorkerTestCase, self).tearDown()

    def get_wikipedia_worker(self, config_override=None):
        config = {
            'transport_name': self.transport_name,
            'worker_name': 'wikitest',
            'sms_transport': 'sphex_sms',
            'api_url': self.url,
            }
        if config_override is not None:
            config.update(config_override)
        return self.get_application(config)

    def assert_config_knob(self, attr, orig, new):
        self.assertEqual(orig, getattr(self.worker, attr))
        self.assertEqual(new, getattr(self.knobbly_worker, attr))

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
        yield self.dispatch(self.mkmsg_in(None))
        self.assertEqual('What would you like to search Wikipedia for?',
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('cthulhu'))
        self.assertEqual('\n'.join([
                    u'1. Cthulhu',
                    u'2. Call of Cthulhu (role-playing game)',
                    u'3. Cthulhu (2007 film)',
                    u'4. Cthulhu (2000 film)',
                    u'5. Cthulhu Mythos',
                    u'6. The Call of Cthulhu',
                    ]),
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('1'))
        self.assertEqual('\n'.join([
                    u'1. Cthulhu',
                    u'2. History',
                    u'3. Geography',
                    u'4. Mountains of Madness',
                    u'5. Lulz',
                    ]),
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('2'))
        self.assertEqual(
            u'The first half of the principal manuscript told a very peculiar '
            u'tale. It appears that on 1 March 1925, a thin, dark young man of'
            u'...\n(Full content sent by SMS.)',
            self.get_dispatched_messages()[-1]['content'])
        [sms_msg] = self._amqp.get_messages('vumi', 'sphex_sms.outbound')
        self.assertEqual(u'The first half of the principal manuscript told a '
            u'very peculiar tale. It appears that on 1 March 1925, a thin, '
            u'dark young man of neurotic and excited aspect had...',
            sms_msg['content'])

    @inlineCallbacks
    def test_search_no_results(self):
        yield self.dispatch(self.mkmsg_in(None))
        self.assertEqual('What would you like to search Wikipedia for?',
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('ncdkiuagdqpowebjkcs'))
        self.assertEqual(
            'Sorry, no Wikipedia results for ncdkiuagdqpowebjkcs',
            self.get_dispatched_messages()[-1]['content'])

    @inlineCallbacks
    def test_search_error(self):
        yield self.dispatch(self.mkmsg_in(None))
        self.assertEqual('What would you like to search Wikipedia for?',
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('.'))
        self.assertEqual(
            'Sorry, there was an error processing your request. Please try '
            'again later.', self.get_dispatched_messages()[-1]['content'])
        self.flushLoggedErrors()

    @inlineCallbacks
    def test_config_knobs(self):
        self.knobbly_worker = yield self.get_wikipedia_worker({
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
