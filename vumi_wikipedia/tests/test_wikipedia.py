"""Tests for vumi.demos.wikipedia."""

import json
from urlparse import urlparse

from twisted.internet.defer import inlineCallbacks

from vumi.application.tests.test_base import ApplicationTestCase
from vumi.message import TransportUserMessage

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
        u'4. Lulz',
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
                'metrics_prefix': 'test.metrics.wikipedia',
                })

    @inlineCallbacks
    def replace_application(self, config):
        # Replace our worker with a different one.
        self._workers.remove(self.worker)
        yield self.worker.stopWorker()
        self.worker = yield self.get_application(config)

    @inlineCallbacks
    def assert_response(self, text, expected, session_event=None):
        yield self.dispatch(self.mkmsg_in(text, session_event=session_event))
        self.assertEqual(
            expected, self.get_outbound_msgs('default')[-1]['content'])

    def get_outbound_msgs(self, endpoint):
        return [m for m in self.get_dispatched_outbound()
                if m['routing_metadata']['endpoint_name'] == endpoint]

    @inlineCallbacks
    def assert_metrics(self, expected_metrics):
        self.worker.metrics._publish_metrics()
        yield self._amqp.kick_delivery()
        [msg] = self._amqp.dispatched['vumi.metrics']['vumi.metrics']
        metrics = {}
        prefix_len = len(self.worker.get_static_config().metrics_prefix) + 1
        for name, _, points in json.loads(msg.body)['datapoints']:
            val = sum(v for ts, v in points)
            if val > 0:
                metrics[name[prefix_len:]] = val
        self.assertEqual(expected_metrics, metrics)

    def start_session(self):
        return self.assert_response(
            None, 'What would you like to search Wikipedia for?')

    def dispatch_sms_content(self, msg):
        msg.set_routing_endpoint('sms_content')
        return self.dispatch(msg)

    @inlineCallbacks
    def tearDown(self):
        yield self.stop_webserver()
        yield super(WikipediaWorkerTestCase, self).tearDown()

    @inlineCallbacks
    def assert_config_knob(self, attr, orig, new):
        msg = self.mkmsg_in()
        worker_config = yield self.worker.get_config(msg)
        knobbly_config = yield self.knobbly_worker.get_config(msg)
        self.assertEqual(orig, getattr(worker_config, attr))
        self.assertEqual(new, getattr(knobbly_config, attr))

    @inlineCallbacks
    def test_make_options(self):
        config = yield self.worker.get_config(self.mkmsg_in())
        self.assertEqual((2, "1. foo\n2. bar"),
                         self.worker.make_options(config, ['foo', 'bar']))

    @inlineCallbacks
    def test_happy_flow(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                })

    @inlineCallbacks
    def test_no_metrics_prefix(self):
        yield self.replace_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'api_url': self.url,
                })
        self.worker.fire_metric('foo')
        # Make sure it's safe to fire a metric when we aren't collecting them.
        self.assertEqual(self.worker.metrics, None)

    @inlineCallbacks
    def test_no_sms_transport(self):
        yield self.replace_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'api_url': self.url,
                'metrics_prefix': 'test.metrics.wikipedia',
                })

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        self.assertEqual(
            [], self._amqp.get_messages('vumi', 'sphex_sms.outbound'))
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                })

    @inlineCallbacks
    def test_invalid_selection_not_digit(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response(
            'six', 'Sorry, invalid selection. Please restart and try again')
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.invalid': 1,
                })

    @inlineCallbacks
    def test_invalid_selection_bad_index(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response(
            '8', 'Sorry, invalid selection. Please restart and try again')
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.invalid': 1,
                })

    @inlineCallbacks
    def test_invalid_selection_later(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response(
            'Hastur', 'Sorry, invalid selection. Please restart and try again')
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.invalid': 1,
                })

    @inlineCallbacks
    def test_search_no_results(self):
        yield self.start_session()
        yield self.assert_response(
            'ncdkiuagdqpowebjkcs',
            'Sorry, no Wikipedia results for ncdkiuagdqpowebjkcs')
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_search.no_results': 1,
                })

    @inlineCallbacks
    def test_search_error(self):
        yield self.start_session()
        yield self.assert_response(
            '.', ('Sorry, there was an error processing your request. Please '
                  'try ' 'again later.'))
        self.flushLoggedErrors()
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_error': 1,
                })

    @inlineCallbacks
    def test_config_knobs(self):
        self.knobbly_worker = yield self.get_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'sms_transport': 'sphex_sms',

                'api_url': 'https://localhost:1337/',
                'accept_gzip': True,
                'user_agent': 'Bob Howard',
                'max_session_length': 200,
                'content_cache_time': 3600,
                'max_ussd_content_length': 180,
                'max_ussd_unicode_length': 80,
                'max_sms_content_length': 300,
                'max_sms_unicode_length': 130,
                })

        yield self.assert_config_knob('api_url', urlparse(self.url),
                                      urlparse('https://localhost:1337/'))
        yield self.assert_config_knob('accept_gzip', False, True)
        yield self.assert_config_knob('user_agent', 'vumi-wikipedia HTTP API',
                                      'Bob Howard')
        yield self.assert_config_knob('max_session_length', 600, 200)
        yield self.assert_config_knob('content_cache_time', 0, 3600)
        yield self.assert_config_knob('max_ussd_content_length', 160, 180)
        yield self.assert_config_knob('max_ussd_unicode_length', 70, 80)
        yield self.assert_config_knob('max_sms_content_length', 160, 300)
        yield self.assert_config_knob('max_sms_unicode_length', 70, 130)

    @inlineCallbacks
    def test_happy_flow_more(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        for _ in range(8):
            msg = self.mkmsg_in('more')
            msg.set_routing_endpoint('sms_content')
            yield self.dispatch(msg)

        sms = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS, sms[0]['content'])
        self.assertEqual('+41791234567', sms[0]['to_addr'])

        self.assertEqual(CTHULHU_MORE, sms[1]['content'])
        self.assertEqual('+41791234567', sms[1]['to_addr'])

        self.assertEqual(CTHULHU_END, sms[-1]['content'])
        self.assertEqual('+41791234567', sms[-1]['to_addr'])
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                'sms_more_content_reply': 8,
                'sms_more_content_reply.1': 1,
                'sms_more_content_reply.2': 1,
                'sms_more_content_reply.3': 1,
                'sms_more_content_reply.4': 1,
                'sms_more_content_reply.5': 1,
                'sms_more_content_reply.6': 1,
                'sms_more_content_reply.7': 1,
                'sms_more_content_reply.8': 1,
                })

    @inlineCallbacks
    def test_more_then_new(self):
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        yield self.dispatch_sms_content(self.mkmsg_in('more'))

        [sms_0, sms_1] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS, sms_0['content'])
        self.assertEqual('+41791234567', sms_0['to_addr'])
        self.assertEqual(CTHULHU_MORE, sms_1['content'])
        self.assertEqual('+41791234567', sms_1['to_addr'])

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_metrics({
                'ussd_session_start': 2,
                'ussd_session_search': 2,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                'sms_more_content_reply': 1,
                'sms_more_content_reply.1': 1,
                })

    @inlineCallbacks
    def test_no_content_cache(self):
        yield self.replace_application({
                'transport_name': self.transport_name,
                'worker_name': 'wikitest',
                'sms_transport': 'sphex_sms',
                'api_url': self.url,
                'incoming_sms_transport': 'sphex_more',
                'content_cache_time': 0,
                })

        # Ensure an exception if `extract_redis` is used anywhere.
        self.worker.extract_redis = None

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])

    @inlineCallbacks
    def test_session_events(self):
        close_session = lambda: self.dispatch(self.mkmsg_in(
                None, session_event=TransportUserMessage.SESSION_CLOSE))
        start_session = lambda: self.assert_response(
            None, 'What would you like to search Wikipedia for?',
            session_event=TransportUserMessage.SESSION_NEW)
        assert_response = lambda txt, rsp: self.assert_response(
            txt, rsp, session_event=TransportUserMessage.SESSION_RESUME)

        yield start_session()
        yield assert_response('cthulhu', CTHULHU_RESULTS)

        config = yield self.worker.get_config(self.mkmsg_in())
        sm = self.worker.get_session_manager(config)

        session = yield self.worker.load_session(sm, '+41791234567')
        self.assertEqual('sections', session['state'])
        yield close_session()
        session = yield self.worker.load_session(sm, '+41791234567')
        self.assertEqual({}, session)

        yield start_session()
        yield assert_response('cthulhu', CTHULHU_RESULTS)
        yield assert_response('1', CTHULHU_SECTIONS)
        yield assert_response('2', CTHULHU_USSD)

        session = yield self.worker.load_session(sm, '+41791234567')
        self.assertEqual('more', session['state'])
        yield close_session()
        session = yield self.worker.load_session(sm, '+41791234567')
        self.assertEqual('more', session['state'])
        yield self.assert_metrics({
                'ussd_session_start': 2,
                'ussd_session_search': 2,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                })
