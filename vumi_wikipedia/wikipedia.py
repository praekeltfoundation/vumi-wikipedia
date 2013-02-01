# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia -*-

import json

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import log
from vumi.application import ApplicationWorker
from vumi.persist.txredis_manager import TxRedisManager
from vumi.components.session import SessionManager
from vumi.message import TransportUserMessage
from vumi.blinkenlights.metrics import MetricManager, Count

from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract
from vumi_wikipedia.text_manglers import normalize_whitespace, ContentFormatter


def mkmenu(options, prefix, start=1):
    return prefix + '\n'.join(
        ['%s. %s' % (idx, opt) for idx, opt in enumerate(options, start)])


class WikipediaWorker(ApplicationWorker):
    """Look up Wikipedia content over USSD, deliver over USSD/SMS.

    Config parameters
    -----------------

    sms_transport : str, optional
        If set, this specifies a different transport for sending SMS replies.
        Otherwise the same transport will be used for both USSD and SMS.

    override_sms_address : str, optional
        If set, this overrides the `to_addr` for SMS replies. This is useful
        for demos where a fake USSD transport is being used but real SMS
        replies are desired.

    api_url : str, optional
        Alternate API URL to use. This can be any MediaWiki deployment,
        although certain assumptions are made about the structure of articels
        that may not be valid outside of Wikipedia.

    accept_gzip : bool, optional
        If `True`, the HTTP client will request gzipped responses. This is
        generally beneficial, although it requires Twisted 11.1 or later.

    user_agent : str, optional
        Override `User-Agent` header on API requests.

    incoming_sms_transport : str, optional
        If set, this specifies a different transport for receiving "more
        content" SMS messages. If unset, incoming SMS messages will be ignored.

    max_ussd_session_length : int, optional
        Lifetime of USSD session in seconds. Defaults to 3 minutes. (If
        `incoming_sms_transport` is set, this should be set to a longer time.)

    content_cache_time : int, optional
        Lifetime of cached article content in seconds. Defaults to 1 hour.

    max_ussd_content_length : int, optional
        Maximum character length of ASCII USSD content. Defaults to 160.

    max_ussd_unicode_length : int, optional
        Maximum character length of unicode USSD content. Defaults to 70.

    max_sms_content_length : int, optional
        Maximum character length of ASCII SMS content. Defaults to 160.

    max_sms_unicode_length : int, optional
        Maximum character length of unicode SMS content. Defaults to 70.

    sentence_break_threshold : int, optional
        If a sentence break is found within this many characters of the end of
        the truncated message, truncate at the sentence break instead of a word
        break. Defaults to 10.

    more_content_postfix : str, optional
        Postfix for SMS content that can be continued. Ignored if
        `incoming_sms_transport` is not set. Defaults to ' (reply for more)'

    no_more_content_postfix : str, optional
        Postfix for SMS content that is complete. Ignored if
        `incoming_sms_transport` is not set. Defaults to ' (end of section)'

    metrics_prefix : str, optional
        Prefix for metrics names. If unset, no metrics will be collected.
    """

    MAX_USSD_SESSION_LENGTH = 3 * 60
    CONTENT_CACHE_TIME = 3600

    MAX_USSD_CONTENT_LENGTH = 160
    MAX_USSD_UNICODE_LENGTH = 70
    MAX_SMS_CONTENT_LENGTH = 160
    MAX_SMS_UNICODE_LENGTH = 70
    SENTENCE_BREAK_THRESHOLD = 10

    MORE_CONTENT_POSTFIX = u' (reply for more)'
    NO_MORE_CONTENT_POSTFIX = u' (end of section)'

    def _opt_config(self, name):
        return self.config.get(name, None)

    def validate_config(self):
        self.sms_transport = self._opt_config('sms_transport')
        self.incoming_sms_transport = self._opt_config(
            'incoming_sms_transport')
        self.override_sms_address = self._opt_config('override_sms_address')

        self.api_url = self._opt_config('api_url')
        self.accept_gzip = self._opt_config('accept_gzip')
        self.user_agent = self._opt_config('user_agent')

        self.max_ussd_session_length = self.config.get(
            'max_ussd_session_length', self.MAX_USSD_SESSION_LENGTH)
        self.content_cache_time = self.config.get(
            'content_cache_time', self.CONTENT_CACHE_TIME)

        self.max_ussd_content_length = self.config.get(
            'max_ussd_content_length', self.MAX_USSD_CONTENT_LENGTH)
        self.max_ussd_unicode_length = self.config.get(
            'max_ussd_unicode_length', self.MAX_USSD_UNICODE_LENGTH)
        self.max_sms_content_length = self.config.get(
            'max_sms_content_length', self.MAX_SMS_CONTENT_LENGTH)
        self.max_sms_unicode_length = self.config.get(
            'max_sms_unicode_length', self.MAX_SMS_UNICODE_LENGTH)
        self.sentence_break_threshold = self.config.get(
            'sentence_break_threshold', self.SENTENCE_BREAK_THRESHOLD)

        if self.incoming_sms_transport:
            self.more_content_postfix = self.config.get(
                'more_content_postfix', self.MORE_CONTENT_POSTFIX)
            self.no_more_content_postfix = self.config.get(
                'no_more_content_postfix', self.NO_MORE_CONTENT_POSTFIX)
        else:
            self.more_content_postfix = u''
            self.no_more_content_postfix = u''

        self.metrics_prefix = self.config.get('metrics_prefix')

    @inlineCallbacks
    def setup_application(self):
        yield self._setup_metrics()
        redis = yield TxRedisManager.from_config(
            self.config.get('redis_manager', {}))
        redis = redis.sub_manager(self.config['worker_name'])

        self.extract_redis = redis.sub_manager('extracts')

        self.session_manager = SessionManager(
            redis.sub_manager('session'),
            max_session_length=self.max_ussd_session_length)

        self.wikipedia = WikipediaAPI(
            self.api_url, self.accept_gzip, self.user_agent)

        self.ussd_formatter = ContentFormatter(
            self.max_ussd_content_length, self.max_ussd_unicode_length,
            sentence_break_threshold=0)

        self.sms_formatter = ContentFormatter(
            self.max_sms_content_length, self.max_sms_unicode_length,
            sentence_break_threshold=self.sentence_break_threshold)

        if self.sms_transport:
            self._setup_outbound_sms_transport()

        if self.incoming_sms_transport:
            yield self._setup_incoming_sms_transport()

    @inlineCallbacks
    def _setup_metrics(self):
        if self.metrics_prefix is None:
            self.metrics = None
            return

        self.metrics = yield self.start_publisher(
            MetricManager, self.metrics_prefix + '.')

        metrics = [
            'ussd_session_start',
            'ussd_session_search',
            'ussd_session_search.no_results',
            'ussd_session_results',
            'ussd_session_results.invalid',
            'ussd_session_sections',
            'ussd_session_sections.invalid',
            'ussd_session_content',
            'sms_more_content_reply',
            'sms_more_content_reply.extra',
            'sms_more_content_reply.no_content',
            'ussd_session_error',
            ]
        for i in range(1, 10):
            metrics.extend([templ % i for templ in [
                        'ussd_session_results.%s',
                        'ussd_session_sections.%s',
                        'sms_more_content_reply.%s',
                        ]])
        for metric in metrics:
            self.metrics.register(Count(metric))

    def fire_metric(self, metric_name, metric_suffix=None, value=1):
        if self.metrics is None or metric_name is None:
            return
        if metric_suffix is not None:
            metric_name = '%s.%s' % (metric_name, metric_suffix)
        self.metrics[metric_name].set(value)
        pass

    def consume_content_sms_event(self, event):
        # TODO: We probably shouldn't just ignore these.
        pass

    def _setup_incoming_sms_transport(self):
        return self.setup_transport_connection(
            'incoming_sms', self.config['incoming_sms_transport'],
            self.consume_sms_message, self.consume_content_sms_event)

    def _setup_outbound_sms_transport(self):
        return self.setup_transport_connection(
            'sms', self.config['sms_transport'],
            self.consume_sms_message, self.consume_content_sms_event)

    def _setup_transport_consumer(self):
        super(WikipediaWorker, self)._setup_transport_consumer()
        if hasattr(self, 'incoming_sms_consumer'):
            self.incoming_sms_consumer.unpause()
        if hasattr(self, 'sms_event_consumer'):
            self.sms_event_consumer.unpause()

    @inlineCallbacks
    def teardown_application(self):
        yield self.session_manager.stop()
        if self.metrics is not None:
            yield self.metrics.stop()

    def make_options(self, options, prefix='', start=1):
        """
        Turn a list of results into an enumerated multiple choice list
        """
        joined = mkmenu(options, prefix, start)
        while len(joined) > self.max_ussd_content_length:
            if not options:
                break
            options = options[:-1]
            joined = mkmenu(options, prefix, start)

        return len(options), joined[:self.max_ussd_content_length]

    @inlineCallbacks
    def _get_cached_extract(self, title):
        key = self.wikipedia.url + ':' + title
        data = yield self.extract_redis.get(key)
        if data is None:
            extract = yield self.wikipedia.get_extract(title)
            # We do this in two steps because our redis clients disagree on
            # what SETEX should look like.
            yield self.extract_redis.set(key, extract.to_json())
            yield self.extract_redis.expire(key, self.content_cache_time)
        else:
            extract = ArticleExtract.from_json(data)
        returnValue(extract)

    def get_extract(self, title):
        if self.content_cache_time > 0:
            return self._get_cached_extract(title)
        return self.wikipedia.get_extract(title)

    def _message_session_event(self, msg):
        # First, check for session parameters on the message.
        if msg['session_event'] == TransportUserMessage.SESSION_NEW:
            return 'new'
        elif msg['session_event'] == TransportUserMessage.SESSION_RESUME:
            return 'resume'
        elif msg['session_event'] == TransportUserMessage.SESSION_CLOSE:
            return 'close'

        # We don't have session data, so guess.
        if msg['content'] is None:
            return 'new'

        return 'resume'

    def close_session(self, msg):
        # We handle all of this in consume_user_message.
        return self.consume_user_message(msg)

    @inlineCallbacks
    def handle_session_result(self, user_id, session):
        if session['state'] is None:
            yield self.session_manager.clear_session(user_id)
        else:
            yield self.save_session(user_id, session)

    @inlineCallbacks
    def load_session(self, user_id):
        session = yield self.session_manager.load_session(user_id)
        if not session:
            returnValue(session)
        returnValue(dict((k, json.loads(v)) for k, v in session.items()))

    def save_session(self, user_id, session):
        if session:
            session = dict((k, json.dumps(v)) for k, v in session.items())
            return self.session_manager.save_session(user_id, session)

    @inlineCallbacks
    def consume_user_message(self, msg):
        log.msg("Received: %s" % (msg.payload,))
        user_id = msg.user()
        session_event = self._message_session_event(msg)
        session = yield self.load_session(user_id)

        if session_event == 'close':
            if ((not self.incoming_sms_transport)
                    or (session and session['state'] != 'more')):
                # Session closed, so clean up and don't reply.
                yield self.session_manager.clear_session(user_id)
            # We never want to respond to close messages, even if we keep the
            # session alive for the "more" handling.
            return

        if (not session) or (session['state'] == 'more'):
            # If we have no session data, treat this as 'new' even if it isn't.
            # Also, new USSD search overrides old "more content" session.
            session_event = 'new'

        if session_event == 'new':
            session = yield self.session_manager.create_session(user_id)
            session['state'] = 'new'

        pfunc = getattr(self, 'process_message_%s' % (session['state'],))
        try:
            session = yield pfunc(msg, session)
            yield self.handle_session_result(user_id, session)
        except:
            log.err()
            self.fire_metric('ussd_session_error')
            self.reply_to(
                msg, 'Sorry, there was an error processing your request. '
                'Please try again later.', False)
            yield self.session_manager.clear_session(user_id)

    def process_message_new(self, msg, session):
        self.fire_metric('ussd_session_start')
        self.reply_to(
            msg, "What would you like to search Wikipedia for?", True)
        session['state'] = 'searching'
        return session

    @inlineCallbacks
    def process_message_searching(self, msg, session):
        self.fire_metric('ussd_session_search')
        query = msg['content'].strip()

        results = yield self.wikipedia.search(query)
        if results:
            count, msgcontent = self.make_options(results)
            session['results'] = json.dumps(results[:count])
            self.reply_to(msg, msgcontent, True)
            session['state'] = 'sections'
        else:
            self.fire_metric('ussd_session_search.no_results')
            self.reply_to(
                msg, 'Sorry, no Wikipedia results for %s' % query, False)
            session['state'] = None
        returnValue(session)

    def select_option(self, options, msg, metric_prefix=None):
        response = msg['content'].strip()

        if response.isdigit():
            try:
                result = options[int(response) - 1]
                self.fire_metric(metric_prefix, int(response))
                return result
            except (KeyError, IndexError):
                pass
        self.fire_metric(metric_prefix, 'invalid')
        self.reply_to(msg,
                      'Sorry, invalid selection. Please restart and try again',
                      False)
        return None

    @inlineCallbacks
    def process_message_sections(self, msg, session):
        self.fire_metric('ussd_session_results')
        selection = self.select_option(json.loads(session['results']), msg,
                                       metric_prefix='ussd_session_results')
        if not selection:
            session['state'] = None
            returnValue(session)

        session['page'] = json.dumps(selection)
        extract = yield self.get_extract(selection)
        results = [selection] + [s.title for s in extract.sections[1:]]
        count, msgcontent = self.make_options([r for r in results])
        session['results'] = json.dumps(results[:count])
        self.reply_to(msg, msgcontent, True)
        session['state'] = 'content'
        returnValue(session)

    @inlineCallbacks
    def process_message_content(self, msg, session):
        self.fire_metric('ussd_session_sections')
        sections = json.loads(session['results'])
        selection = self.select_option(sections, msg,
                                       metric_prefix='ussd_session_sections')
        if not selection:
            session['state'] = None
            returnValue(session)
        page = json.loads(session['page'])
        extract = yield self.get_extract(page)
        content = extract.sections[int(msg['content'].strip()) - 1].full_text()
        session['sms_content'] = normalize_whitespace(content)
        session['sms_offset'] = 0
        ussd_cont = self.ussd_formatter.format(
            content, '\n(Full content sent by SMS.)')
        self.fire_metric('ussd_session_content')
        self.reply_to(msg, ussd_cont, False)
        if self.sms_transport:
            session = yield self.send_sms_content(msg, session)
        if self.incoming_sms_transport is None:
            session['state'] = None
        else:
            session['state'] = 'more'
        returnValue(session)

    def send_sms_content(self, msg, session):
        content_len, sms_content = self.sms_formatter.format_more(
            session['sms_content'], session['sms_offset'],
            self.more_content_postfix, self.no_more_content_postfix)
        session['sms_offset'] = session['sms_offset'] + content_len + 1
        if session['sms_offset'] >= len(session['sms_content']):
            session['state'] = None

        bmsg = msg.reply(sms_content)
        bmsg['transport_name'] = self.sms_transport
        bmsg['transport_type'] = 'sms'
        if self.override_sms_address:
            bmsg['to_addr'] = self.override_sms_address
        self.sms_publisher.publish_message(bmsg)

        return session

    @inlineCallbacks
    def consume_sms_message(self, msg):
        log.msg("Received SMS: %s" % (msg.payload,))

        # This is to exclude some spurious messages we might receive.
        if msg['content'] is None:
            log.msg("No content, ignoring.")
            return

        user_id = msg.user()

        session = yield self.load_session(user_id)
        self.fire_metric('sms_more_content_reply')
        if not session:
            # TODO: Reply with error?
            self.fire_metric('sms_more_content_reply.no_content')
            return

        more_messages = session.get('more_messages', 0) + 1
        session['more_messages'] = more_messages
        if more_messages > 9:
            more_messages = 'extra'
        self.fire_metric('sms_more_content_reply', more_messages)

        try:
            session = yield self.send_sms_content(msg, session)
            yield self.handle_session_result(user_id, session)
        except:
            log.err()
            # TODO: Reply with error?
            yield self.session_manager.clear_session(user_id)
