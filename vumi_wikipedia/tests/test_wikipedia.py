"""Tests for vumi.demos.wikipedia."""

import json
import hashlib
import logging
from urlparse import urlparse
from pkg_resources import resource_stream

from twisted.internet.defer import inlineCallbacks

from vumi.application.tests.helpers import ApplicationHelper
from vumi.message import TransportUserMessage
from vumi.tests.helpers import VumiTestCase
from vumi.tests.utils import LogCatcher

from vumi_wikipedia.wikipedia import WikipediaWorker, log_escape
from vumi_wikipedia.tests.test_wikipedia_api import (
    FakeHTTPTestCaseMixin, WIKIPEDIA_RESPONSES)
USS_RESPONSES = json.load(
    resource_stream(__name__, 'uss_responses.json'))


CTHULHU_RESULTS = '\n'.join([
        u'1. Cthulhu',
        u'2. Call of Cthulhu (role-playing game)',
        u'3. Cthulhu (2007 film)',
        u'4. Cthulhu (2000 film)',
        u'5. Cthulhu Mythos',
        u'6. The Call of Cthulhu',
        ])

CTHULHU_CIRRUS_RESULTS = '\n'.join([
    '1. Cthulhu',
    '2. Cthulhu Mythos',
    '3. Cthulhu Mythos anthology',
    '4. Call of Cthulhu (role-playing game)',
    '5. Cthulhu Mythos deities',
    '6. The Call of Cthulhu',
    '7. I, Cthulhu',
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

CTHULHU_SMS_NO_SUFFIX = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark young man of neurotic and '
    u'excited aspect ...')

CTHULHU_SMS = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark young man of neurotic ... '
    u'(reply for more)')

CTHULHU_SMS_NO_SPACE = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark young man of neurotic '
    u'...(reply for more)')

CTHULHU_SMS_WITH_URL = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, ... '
    u'http://en.wikipedia.org/wiki/Cthulhu '
    u'(reply for more)')

CTHULHU_SMS_WITH_SHORTENED_URL = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March 1925, a thin, dark ... '
    u'http://wtxt.io/aaa '
    u'(reply for more)')

CTHULHU_SMS_WITH_AFRIKAANS_URL = (
    u'The first half of the principal manuscript told a very peculiar tale. '
    u'It appears that on 1 March ... '
    u'http://af.m.wikipedia.org/wiki/Cthulhu '
    u'(reply for more)')

CTHULHU_MORE = (
    u'...and excited aspect had called upon Professor Angell bearing the '
    u'singular clay bas-relief, which was then exceedingly damp and fresh. '
    u'... (reply for more)')

CTHULHU_MORE_FOR_SMS_WITH_URL = (
    u'...a thin, dark young man of neurotic and excited aspect had called '
    u'upon Professor Angell bearing the singular clay bas-relief, which was '
    u'... (reply for more)')

CTHULHU_END = (
    u'...anxious to preserve its conservatism, had found him quite hopeless. '
    u'(end of section)')

CTHULHU_END_NO_SPACE = (
    u'...conservatism, had found him quite hopeless.(end of section)')

CTHULHU_END_NO_SUFFIX = (
    u'...Club, anxious to preserve its conservatism, had found him quite '
    u'hopeless.')

CTHULHU_END_FOR_SMS_WITH_URL = (
    u'...Even the Providence Art Club, anxious to preserve its conservatism, '
    u'had found him quite hopeless. '
    u'(end of section)')

WIKIPEDIA_RESULTS = u'1. Wikipedia\n2. Wikip\xe9dia\n3. Main Page'
WIKIPEDIA_SECTIONS = u'1. Wikip\xe9dia'
WIKIPEDIA_USSD = (
    u'Wikip\xe9dia may refer to:\nFrench ...\n(Full content sent by SMS.)')
WIKIPEDIA_SMS = (
    u'Wikip\xe9dia may refer to: French Wikipedia ... '
    u'(reply for more)')

WIKIPEDIA_RESULTS_TL = u'1. Wikipedia\n2. Wikipedia\n3. Main Page'
WIKIPEDIA_SECTIONS_TL = u'1. Wikipedia'
WIKIPEDIA_USSD_TL = (
    u'Wikipedia may refer to:\nFrench Wikipedia\nPortuguese Wikipedia\n'
    u'Hungarian Wikipedia\nSlovak Wikipedia\n(Full content sent by SMS.)')
WIKIPEDIA_SMS_TL = (
    u'Wikipedia may refer to: French Wikipedia Portuguese '
    u'Wikipedia Hungarian Wikipedia Slovak Wikipedia '
    u'(end of section)')


class WikipediaWorkerTestCase(VumiTestCase, FakeHTTPTestCaseMixin):

    def setUp(self):
        self.app_helper = self.add_helper(ApplicationHelper(
            WikipediaWorker, transport_type='ussd'))
        self.fake_api = self.start_webserver(WIKIPEDIA_RESPONSES)
        self.fake_uss = self.start_webserver(USS_RESPONSES)

    @inlineCallbacks
    def setup_application(self, config={}, use_defaults=True):
        defaults = {
            'worker_name': 'wikitest',
            'api_url': self.fake_api.url,
            'metrics_prefix': 'test.metrics.wikipedia',
            'hash_algorithm': 'sha256',
            'secret_key': 'foo',
            'user_hash_char_limit': 128,
        }
        defaults.update(config)
        if use_defaults:
            config = defaults
        self.worker = yield self.app_helper.get_application(config)

    def make_dispatch_sms(self, content, **kw):
        return self.app_helper.make_dispatch_inbound(
            content, transport_type='sms', endpoint='sms_content', **kw)

    @inlineCallbacks
    def assert_response(self, text, expected, session_event=None):
        yield self.app_helper.make_dispatch_inbound(
            text, session_event=session_event)
        self.assertEqual(
            expected, self.get_outbound_msgs('default')[-1]['content'])

    def get_outbound_msgs(self, endpoint):
        return [m for m in self.app_helper.get_dispatched_outbound()
                if m['routing_metadata']['endpoint_name'] == endpoint]

    @inlineCallbacks
    def assert_metrics(self, expected_metrics):
        self.worker.metrics._publish_metrics()
        yield self.app_helper.kick_delivery()
        broker = self.app_helper.worker_helper.broker
        [msg] = broker.dispatched['vumi.metrics']['vumi.metrics']
        metrics = {}
        prefix_len = len(self.worker.get_static_config().metrics_prefix) + 1
        for name, _, points in json.loads(msg.body)['datapoints']:
            val = sum(v for ts, v in points)
            if val > 0:
                metrics[name[prefix_len:]] = val
        for k, v in expected_metrics.items():
            if isinstance(v, tuple):
                low, high = v
                actual = metrics[k]
                self.assertTrue(
                    low <= actual <= high,
                    'Expected %s between %s and %s, was %s.' % (
                        k, low, high, actual))
                expected_metrics[k] = actual
        self.assertEqual(expected_metrics, metrics)

    def start_session(self):
        return self.assert_response(
            None, 'What would you like to search Wikipedia for?')

    @inlineCallbacks
    def assert_config_knob(self, attr, orig, new):
        msg = self.app_helper.make_inbound(None)
        worker_config = yield self.worker.get_config(msg)
        knobbly_config = yield self.knobbly_worker.get_config(msg)
        self.assertEqual(orig, getattr(worker_config, attr))
        self.assertEqual(new, getattr(knobbly_config, attr))

    @inlineCallbacks
    def test_make_options(self):
        yield self.setup_application()
        config = yield self.worker.get_config(
            self.app_helper.make_inbound(None))
        self.assertEqual((2, "1. foo\n2. bar"),
                         self.worker.make_options(config, ['foo', 'bar']))

    @inlineCallbacks
    def test_happy_flow(self):
        yield self.setup_application()
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
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_include_url_in_sms_config(self):
        yield self.setup_application({
            'include_url_in_sms': True,
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS_WITH_URL, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])
        yield self.assert_metrics({
            'ussd_session_start': 1,
            'ussd_session_search': 1,
            'ussd_session_results': 1,
            'ussd_session_results.1': 1,
            'ussd_session_sections': 1,
            'ussd_session_sections.2': 1,
            'ussd_session_content': 1,
            'wikipedia_search_call': (0, 1),
            'wikipedia_extract_call': (0, 1),
        })

    @inlineCallbacks
    def test_include_shortened_url_in_sms_config(self):
        yield self.setup_application({
            'include_url_in_sms': True,
            'mobi_url_host': 'http://en.m.wikipedia.org',
            'shortening_api_url': self.fake_uss.url,
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS_WITH_SHORTENED_URL, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])
        yield self.assert_metrics({
            'ussd_session_start': 1,
            'ussd_session_search': 1,
            'ussd_session_results': 1,
            'ussd_session_results.1': 1,
            'ussd_session_sections': 1,
            'ussd_session_sections.2': 1,
            'ussd_session_content': 1,
            'wikipedia_search_call': (0, 1),
            'wikipedia_extract_call': (0, 1),
        })

    @inlineCallbacks
    def test_include_url_in_sms_no_suffix_space(self):
        yield self.setup_application({
            'include_url_in_sms': True,
            'msg_more_content_suffix': '(reply for more)',
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS_WITH_URL, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])
        yield self.assert_metrics({
            'ussd_session_start': 1,
            'ussd_session_search': 1,
            'ussd_session_results': 1,
            'ussd_session_results.1': 1,
            'ussd_session_sections': 1,
            'ussd_session_sections.2': 1,
            'ussd_session_content': 1,
            'wikipedia_search_call': (0, 1),
            'wikipedia_extract_call': (0, 1),
        })

    @inlineCallbacks
    def test_different_host_for_sms_url(self):
        yield self.setup_application({
            'include_url_in_sms': True,
            'mobi_url_host': 'http://af.m.wikipedia.org',
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS_WITH_AFRIKAANS_URL, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])
        yield self.assert_metrics({
            'ussd_session_start': 1,
            'ussd_session_search': 1,
            'ussd_session_results': 1,
            'ussd_session_results.1': 1,
            'ussd_session_sections': 1,
            'ussd_session_sections.2': 1,
            'ussd_session_content': 1,
            'wikipedia_search_call': (0, 1),
            'wikipedia_extract_call': (0, 1),
        })

    @inlineCallbacks
    def test_no_url_in_more_sms(self):
        yield self.setup_application({
            'include_url_in_sms': True,
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        for _ in range(8):
            yield self.make_dispatch_sms('more')

        sms = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS_WITH_URL, sms[0]['content'])
        self.assertEqual('+41791234567', sms[0]['to_addr'])

        self.assertEqual(CTHULHU_MORE_FOR_SMS_WITH_URL, sms[1]['content'])
        self.assertEqual('+41791234567', sms[1]['to_addr'])

        self.assertEqual(CTHULHU_END_FOR_SMS_WITH_URL, sms[-1]['content'])
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
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_no_metrics_prefix(self):
        yield self.setup_application({
            'worker_name': 'wikitest',
            'api_url': self.fake_api.url,
        }, use_defaults=False)
        # Make sure it's safe to fire a metric when we aren't collecting them.
        self.worker.fire_metric(None, 'foo')
        self.assertEqual(self.worker.metrics, None)
        # Make sure it's safe to use a timer metric when we aren't collecting.
        with self.worker.get_timer_metric(None, 'foo'):
            pass
        self.assertEqual(self.worker.metrics, None)

    @inlineCallbacks
    def test_no_sms_config(self):
        yield self.setup_application({
            'send_sms_content': False,
        })

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        self.assertEqual([], self.get_outbound_msgs('sms_content'))
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_invalid_selection_not_digit(self):
        yield self.setup_application()
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response(
            'six', 'Sorry, invalid selection. Please restart and try again')
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.invalid': 1,
                'wikipedia_search_call': (0, 1),
                })

    @inlineCallbacks
    def test_invalid_selection_bad_index(self):
        yield self.setup_application()
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response(
            '8', 'Sorry, invalid selection. Please restart and try again')
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.invalid': 1,
                'wikipedia_search_call': (0, 1),
                })

    @inlineCallbacks
    def test_invalid_selection_later(self):
        yield self.setup_application()
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
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_search_no_results(self):
        yield self.setup_application()
        yield self.start_session()
        yield self.assert_response(
            'ncdkiuagdqpowebjkcs',
            'Sorry, no Wikipedia results for ncdkiuagdqpowebjkcs')
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_search.no_results': 1,
                'wikipedia_search_call': (0, 1),
                })

    @inlineCallbacks
    def test_search_error(self):
        yield self.setup_application()
        yield self.start_session()
        with LogCatcher(log_level=logging.WARNING) as log:
            yield self.assert_response('.', (
                'Sorry, there was an error processing your request. Please '
                'try again later.'))
            [warning] = log.logs
            self.assertTrue('srsearch-text-disabled' in warning['message'][0])
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_error': 1,
                'wikipedia_search_call': (0, 1),
                })

    @inlineCallbacks
    def test_config_knobs(self):
        yield self.setup_application()
        self.knobbly_worker = yield self.app_helper.get_application({
            'worker_name': 'wikitest',
            'sms_transport': 'sphex_sms',

            'api_url': 'https://localhost:1337/',
            'api_timeout': 10,
            'accept_gzip': True,
            'user_agent': 'Bob Howard',
            'max_session_length': 200,
            'content_cache_time': 3600,
            'max_ussd_content_length': 180,
            'max_ussd_unicode_length': 80,
            'max_sms_content_length': 300,
            'max_sms_unicode_length': 130,
        })

        yield self.assert_config_knob('api_url', urlparse(self.fake_api.url),
             urlparse('https://localhost:1337/'))
        yield self.assert_config_knob('accept_gzip', False, True)
        yield self.assert_config_knob(
            'user_agent',
            'vumi-wikipedia/1.0 (https://github.com/praekelt/vumi-wikipedia; '
            'support@vumi.org)',
            'Bob Howard')
        yield self.assert_config_knob('max_session_length', 600, 200)
        yield self.assert_config_knob('content_cache_time', 0, 3600)
        yield self.assert_config_knob('max_ussd_content_length', 160, 180)
        yield self.assert_config_knob('max_ussd_unicode_length', 70, 80)
        yield self.assert_config_knob('max_sms_content_length', 160, 300)
        yield self.assert_config_knob('max_sms_unicode_length', 70, 130)
        yield self.assert_config_knob('api_timeout', 5, 10)

    @inlineCallbacks
    def test_search_custom_backend(self):
        yield self.setup_application({
            'search_backend': 'CirrusSearch',
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_CIRRUS_RESULTS)

    @inlineCallbacks
    def test_happy_flow_more(self):
        yield self.setup_application()
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        for _ in range(8):
            yield self.make_dispatch_sms('more')

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
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_happy_flow_more_no_suffix(self):
        yield self.setup_application({
            'msg_more_content_suffix': '',
            'msg_no_more_content_suffix': '',
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        for _ in range(7):
            yield self.make_dispatch_sms('more')

        sms = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS_NO_SUFFIX, sms[0]['content'])
        self.assertEqual('+41791234567', sms[0]['to_addr'])

        self.assertEqual(CTHULHU_END_NO_SUFFIX, sms[-1]['content'])
        self.assertEqual('+41791234567', sms[-1]['to_addr'])
        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                'sms_more_content_reply': 7,
                'sms_more_content_reply.1': 1,
                'sms_more_content_reply.2': 1,
                'sms_more_content_reply.3': 1,
                'sms_more_content_reply.4': 1,
                'sms_more_content_reply.5': 1,
                'sms_more_content_reply.6': 1,
                'sms_more_content_reply.7': 1,
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_happy_flow_more_no_space(self):
        yield self.setup_application({
            'msg_more_content_suffix': '(reply for more)',
            'msg_no_more_content_suffix': '(end of section)',
        })
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        for _ in range(8):
            yield self.make_dispatch_sms('more')

        sms = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS_NO_SPACE, sms[0]['content'])
        self.assertEqual('+41791234567', sms[0]['to_addr'])

        self.assertEqual(CTHULHU_END_NO_SPACE, sms[-1]['content'])
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
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_more_then_new(self):
        yield self.setup_application()
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        yield self.make_dispatch_sms('more')

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
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_no_content_cache(self):
        yield self.setup_application({
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
    def test_content_cache(self):
        yield self.setup_application({
            'content_cache_time': 10,
        })

        cache_keys = yield self.worker.extract_redis.keys('*')
        self.assertEqual([], cache_keys)

        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)
        yield self.assert_response('2', CTHULHU_USSD)

        cache_keys = yield self.worker.extract_redis.keys('*')
        self.assertEqual(['%s:Cthulhu' % (self.fake_api.url,)], cache_keys)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])

    @inlineCallbacks
    def test_session_events(self):
        yield self.setup_application()
        close_session = lambda: self.app_helper.make_dispatch_inbound(
                None, session_event=TransportUserMessage.SESSION_CLOSE)
        start_session = lambda: self.assert_response(
            None, 'What would you like to search Wikipedia for?',
            session_event=TransportUserMessage.SESSION_NEW)
        assert_response = lambda txt, rsp: self.assert_response(
            txt, rsp, session_event=TransportUserMessage.SESSION_RESUME)

        yield start_session()
        yield assert_response('cthulhu', CTHULHU_RESULTS)

        config = yield self.worker.get_config(
            self.app_helper.make_inbound(None))
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
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_more_mid_session(self):
        yield self.setup_application()
        yield self.start_session()
        yield self.assert_response('cthulhu', CTHULHU_RESULTS)
        yield self.assert_response('1', CTHULHU_SECTIONS)

        yield self.make_dispatch_sms('more')
        self.assertEqual([], self.get_outbound_msgs('sms_content'))

        yield self.assert_response('2', CTHULHU_USSD)

        yield self.make_dispatch_sms('more')
        [sms_0, sms_1] = self.get_outbound_msgs('sms_content')
        self.assertEqual(CTHULHU_SMS, sms_0['content'])
        self.assertEqual('+41791234567', sms_0['to_addr'])
        self.assertEqual(CTHULHU_MORE, sms_1['content'])
        self.assertEqual('+41791234567', sms_1['to_addr'])

        yield self.assert_metrics({
                'ussd_session_start': 1,
                'ussd_session_search': 1,
                'ussd_session_results': 1,
                'ussd_session_results.1': 1,
                'ussd_session_sections': 1,
                'ussd_session_sections.2': 1,
                'ussd_session_content': 1,
                'sms_more_content_reply': 2,
                'sms_more_content_reply.1': 1,
                'wikipedia_search_call': (0, 1),
                'wikipedia_extract_call': (0, 1),
                })

    @inlineCallbacks
    def test_unicode_content(self):
        yield self.setup_application({
            'transliterate_unicode': False,
        })

        yield self.start_session()
        yield self.assert_response('wikipedia', WIKIPEDIA_RESULTS)
        yield self.assert_response('2', WIKIPEDIA_SECTIONS)
        yield self.assert_response('1', WIKIPEDIA_USSD)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(WIKIPEDIA_SMS, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])

    @inlineCallbacks
    def test_transliterate_unicode(self):
        yield self.setup_application({
            'transliterate_unicode': True,
        })

        yield self.start_session()
        yield self.assert_response('wikipedia', WIKIPEDIA_RESULTS_TL)
        yield self.assert_response('2', WIKIPEDIA_SECTIONS_TL)
        yield self.assert_response('1', WIKIPEDIA_USSD_TL)

        [sms_msg] = self.get_outbound_msgs('sms_content')
        self.assertEqual(WIKIPEDIA_SMS_TL, sms_msg['content'])
        self.assertEqual('+41791234567', sms_msg['to_addr'])

    @inlineCallbacks
    def test_logging_hash(self):
        def mock_hash(_, user_id):
            return 'foo'

        self.patch(WikipediaWorker, 'hash_user', mock_hash)
        yield self.setup_application()
        with LogCatcher() as log:
            yield self.start_session()
            [entry] = log.logs
            self.assertTrue(
                'WIKI\tfoo\tsphex\tussd\t\tstart\tNone' in entry['message'])

    @inlineCallbacks
    def test_user_hash(self):
        expected_user_hash = hashlib.sha256('+41791234567' + 'foo').hexdigest()
        expected_entry = 'WIKI\t%s\tsphex\tussd\t\tstart\tNone' % (
            expected_user_hash)
        yield self.setup_application({'user_hash_char_limit': -1})
        with LogCatcher() as log:
            yield self.start_session()
            [entry] = log.logs
            self.assertTrue(expected_entry in entry['message'])

    @inlineCallbacks
    def test_hash_truncating(self):
        expected_user_hash = hashlib.sha256('+41791234567' + 'foo').hexdigest()
        expected_entry = 'WIKI\t%s\tsphex\tussd\t\tstart\tNone' % (
            expected_user_hash[:64])
        yield self.setup_application({'user_hash_char_limit': 64})
        with LogCatcher() as log:
            yield self.start_session()
            [entry] = log.logs
            self.assertTrue(expected_entry in entry['message'])

    @inlineCallbacks
    def test_broken_session(self):
        yield self.setup_application()
        msg = self.app_helper.make_inbound(None)
        cfg = yield self.worker.get_config(msg)
        session_manager = self.worker.get_session_manager(cfg)
        badsession = {'unexpectedfield': u'nostate'}
        yield self.worker.save_session(session_manager, msg.user(), badsession)
        with LogCatcher(log_level=logging.WARNING) as log:
            yield self.start_session()
            [warning] = log.logs
            expected_warning = 'Bad session, resetting: %s' % (badsession,)
            self.assertTrue(expected_warning in warning['message'])

    @inlineCallbacks
    def test_logging_message_content(self):
        def mock_hash(_, user_id):
            return 'foo'

        self.patch(WikipediaWorker, 'hash_user', mock_hash)
        yield self.setup_application()
        with LogCatcher() as log:
            yield self.start_session()
            yield self.assert_response('\tcthulhu\n', CTHULHU_RESULTS)
            [entry1, entry2] = [
                entry for entry in log.logs
                if 'HTTP11Client' not in entry['message'][0]]
            self.assertEqual(
                'WIKI\tfoo\tsphex\tussd\t\tstart\tNone', entry1['message'][0])
            self.assertEqual(
                ("WIKI\tfoo\tsphex\tussd\t\ttitles\t\\tcthulhu\\n\tfound=9"
                 "\tshown=6"), entry2['message'][0])

    @inlineCallbacks
    def test_wikipedia_api_params(self):
        yield self.setup_application()
        msg = self.app_helper.make_inbound(None)
        worker_config = yield self.worker.get_config(msg)
        api = self.worker.get_wikipedia_api(worker_config)
        self.assertEqual(api.url, worker_config.api_url.geturl())
        self.assertEqual(api.gzip, worker_config.accept_gzip)
        self.assertEqual(api.user_agent, worker_config.user_agent)
        self.assertEqual(api.api_timeout, worker_config.api_timeout)

    def test_log_escape(self):
        self.assertEqual('None', log_escape(None))
        self.assertEqual('{}', log_escape({}))
        self.assertEqual('', log_escape(''))
        self.assertEqual('"', log_escape('"'))
        self.assertEqual("'", log_escape("'"))
        self.assertEqual('"\'', log_escape('"\''))
        self.assertEqual('"\'', log_escape(u'"\''))

    @inlineCallbacks
    def test_basic_auth_url(self):
        yield self.setup_application()
        header, url = self.worker.get_basic_auth_header('http://wtxt.io/api/')
        self.assertEqual(header, None)
        self.assertEqual(url, 'http://wtxt.io/api/')

        header, url = self.worker.get_basic_auth_header('http://wtxt.io:80/api/')
        self.assertEqual(header, None)
        self.assertEqual(url, 'http://wtxt.io:80/api/')

        header, url = self.worker.get_basic_auth_header('http://test:user@wtxt.io/api/')
        self.assertEqual(header, {'Authorization': 'Basic dGVzdDp1c2Vy'})
        self.assertEqual(url, 'http://wtxt.io/api/')

        header, url = self.worker.get_basic_auth_header('http://test:user@wtxt.io:80/api/')
        self.assertEqual(header, {'Authorization': 'Basic dGVzdDp1c2Vy'})
        self.assertEqual(url, 'http://wtxt.io:80/api/')
