# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia -*-

import json

from twisted.internet.defer import inlineCallbacks, returnValue
from vumi import log
from vumi.application import ApplicationWorker
from vumi.persist.txredis_manager import TxRedisManager
from vumi.components.session import SessionManager
from vumi.message import TransportUserMessage
from vumi.blinkenlights.metrics import MetricManager, Count
from vumi.config import (
    ConfigUrl, ConfigBool, ConfigText, ConfigInt, ConfigDict)

from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract
from vumi_wikipedia.text_manglers import (
    ContentFormatter, normalize_whitespace, transliterate_unicode,
    minimize_unicode)


def mkmenu(options, prefix, start=1):
    return prefix + '\n'.join(
        ['%s. %s' % (idx, opt) for idx, opt in enumerate(options, start)])


class WikipediaConfig(ApplicationWorker.CONFIG_CLASS):
    api_url = ConfigUrl(
        "URL for the MediaWiki API to query. This defaults to the English"
        " Wikipedia. Any recent enough MediaWiki installation should be fine,"
        " although certain assumptions are made about the structure of"
        " articles that may not hold outside of Wikipedia.",
        default='http://en.wikipedia.org/w/api.php')

    accept_gzip = ConfigBool(
        "If `True`, the HTTP client will request gzipped responses. This is"
        " generally beneficial, although it requires Twisted 11.1 or later.",
        default=False)

    user_agent = ConfigText(
        "Value of the `User-Agent` header on API requests.",
        default=('vumi-wikipedia/1.0 (https://github.com/praekelt/vumi-'
                 'wikipedia; support@vumi.org)'))

    max_session_length = ConfigInt(
        "Lifetime of query session in seconds. This includes the lifetime of"
        " the content kept for further SMS deliveries.", default=600)

    max_ussd_content_length = ConfigInt(
        "Maximum character length of ASCII USSD content.", default=160)

    max_ussd_unicode_length = ConfigInt(
        "Maximum character length of unicode USSD content.", default=70)

    max_sms_content_length = ConfigInt(
        "Maximum character length of ASCII SMS content.", default=160)

    max_sms_unicode_length = ConfigInt(
        "Maximum character length of unicode SMS content.", default=70)

    sentence_break_threshold = ConfigInt(
        "If a sentence break is found within this many characters of the end"
        " of the truncated message, truncate at the sentence break instead of"
        " a word break.", default=10)

    transliterate_unicode = ConfigBool(
        "Set this to `True` to transliterate any non-ASCII chars. Requires"
        " unidecode lib.", default=False)

    minimize_text = ConfigBool(
        "Set this to `True` to attempt to shorten text by removing unnecessary"
        " chars.", default=False)

    send_sms_content = ConfigBool(
        "Set this to `False` to suppress the sending of content via SMS.",
        default=True)

    send_more_sms_content = ConfigBool(
        "Set this to `False` to ignore requests for more content via SMS.",
        default=True)

    metrics_prefix = ConfigText(
        "Prefix for metrics names. If unset, no metrics will be collected.",
        static=True)

    content_cache_time = ConfigInt(
        "Lifetime of cached article content in seconds. If unset, no caching"
        " will be done.", static=True, default=0)

    redis_manager = ConfigDict(
        "Redis connection configuration.", static=True, default={})

    msg_prompt = ConfigText(
        'Initial prompt shown to the users',
        default=u'What would you like to search Wikipedia for?')

    msg_no_results = ConfigText(
        'No results found message, with one string parameter',
        default=u'Sorry, no Wikipedia results for %s')

    msg_error = ConfigText(
        'Generic internal error message',
        default=u'Sorry, there was an error processing your request.'
        ' Please try again later.')

    msg_invalid_section = ConfigText(
        'User picked incorrect section',
        default=u'Sorry, invalid selection. Please restart and try again')

    msg_more_content_suffix = ConfigText(
        "Suffix for SMS content that can be continued. An empty string"
        " should be specified if there is no incoming SMS connection.",
        default=' (reply for more)')

    msg_no_more_content_suffix = ConfigText(
        "Suffix for SMS content that is complete. An empty string should be "
        " specified if there is no incoming SMS connection.",
        default=' (end of section)')

    msg_ussd_suffix = ConfigText(
        'Message to add at the end of the truncated USSD result',
        default=u'\n(Full content sent by SMS.)')


class WikipediaWorker(ApplicationWorker):
    """Look up Wikipedia content over USSD, deliver over USSD/SMS.

    Please note: This version is significantly different from previous versions
    and requires the use of an endpoints-aware dispatcher to connect it to the
    various transports.
    """

    CONFIG_CLASS = WikipediaConfig
    ALLOWED_ENDPOINTS = frozenset(['default', 'sms_content'])

    @inlineCallbacks
    def setup_application(self):
        config = self.get_static_config()
        yield self._setup_metrics(config.metrics_prefix)

        redis = yield TxRedisManager.from_config(config.redis_manager)
        self._redis = redis.sub_manager(self.config['worker_name'])

        if config.content_cache_time:
            self.extract_redis = redis.sub_manager('extracts')

        self.connectors[self.transport_name].set_inbound_handler(
            self.consume_content_sms_message, 'sms_content')
        self.connectors[self.transport_name].set_event_handler(
            self.consume_content_sms_event, 'sms_content')

    def get_redis(self, config):
        return self._redis

    def get_session_manager(self, config):
        return SessionManager(
            self.get_redis(config).sub_manager('session'),
            max_session_length=config.max_session_length)

    def get_wikipedia_api(self, config):
        return WikipediaAPI(
            config.api_url.geturl(), config.accept_gzip, config.user_agent)

    def get_sms_formatter(self, config):
        return ContentFormatter(
            config.max_sms_content_length, config.max_sms_unicode_length,
            sentence_break_threshold=config.sentence_break_threshold)

    def get_ussd_formatter(self, config):
        return ContentFormatter(
            config.max_ussd_content_length, config.max_ussd_unicode_length,
            sentence_break_threshold=0)

    @inlineCallbacks
    def _setup_metrics(self, metrics_prefix):
        if metrics_prefix is None:
            self.metrics = None
            return

        self.metrics = yield self.start_publisher(
            MetricManager, metrics_prefix + '.')

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

    @inlineCallbacks
    def teardown_application(self):
        yield self._redis._close()
        if self.metrics is not None:
            yield self.metrics.stop()

    def make_options(self, config, options, prefix='', start=1):
        """
        Turn a list of results into an enumerated multiple choice list
        """
        # Normalize all text for USSD (minimize, transliterate, etc)
        options = [self.normalize_content(config, v)[0] for v in options]

        joined = mkmenu(options, prefix, start)
        while len(joined) > config.max_ussd_content_length:
            if not options:
                break
            options = options[:-1]
            joined = mkmenu(options, prefix, start)

        return len(options), joined[:config.max_ussd_content_length]

    @inlineCallbacks
    def _get_cached_extract(self, config, title):
        wikipedia = self.get_wikipedia_api(config)
        key = ':'.join([wikipedia.url, title])
        data = yield self.extract_redis.get(key)
        if data is None:
            extract = yield self.get_wikipedia_api(config).get_extract(title)
            # We do this in two steps because our redis clients disagree on
            # what SETEX should look like.
            yield self.extract_redis.set(key, extract.to_json())
            yield self.extract_redis.expire(key, config.content_cache_time)
        else:
            extract = ArticleExtract.from_json(data)
        returnValue(extract)

    def get_extract(self, config, title):
        if config.content_cache_time > 0:
            return self._get_cached_extract(config, title)
        return self.get_wikipedia_api(config).get_extract(title)

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
    def handle_session_result(self, session_manager, user_id, session):
        if session['state'] is None:
            yield session_manager.clear_session(user_id)
        else:
            yield self.save_session(session_manager, user_id, session)

    @inlineCallbacks
    def load_session(self, session_manager, user_id):
        session = yield session_manager.load_session(user_id)
        if not session:
            returnValue(session)
        returnValue(dict((k, json.loads(v)) for k, v in session.items()))

    def save_session(self, session_manager, user_id, session):
        if session:
            session = dict((k, json.dumps(v)) for k, v in session.items())
            return session_manager.save_session(user_id, session)

    def log_action(self, msg, action, **kw):
        # the empty value should later be replaced with the network operator ID
        log_parts = [
            'WIKI', msg.user(), msg['transport_name'], msg['transport_type'],
            '', action, msg['content'],
        ] + [u'%s=%r' % (k, v) for (k, v) in kw.items()]

        log.msg(u'\t'.join(unicode(s) for s in log_parts).encode('utf8'))

    @inlineCallbacks
    def consume_user_message(self, msg):
        # log.msg("Received: %s" % (msg.payload,))
        config = yield self.get_config(msg)
        user_id = msg.user()
        session_event = self._message_session_event(msg)
        session_manager = self.get_session_manager(config)
        session = yield self.load_session(session_manager, user_id)

        if session_event == 'close':
            if ((not config.send_sms_content)
                    or (session and session['state'] != 'more')):
                # Session closed, so clean up and don't reply.
                yield session_manager.clear_session(user_id)
            # We never want to respond to close messages, even if we keep the
            # session alive for the "more" handling.
            return

        if (not session) or (session['state'] == 'more'):
            # If we have no session data, treat this as 'new' even if it isn't.
            # Also, new USSD search overrides old "more content" session.
            session_event = 'new'

        if session_event == 'new':
            session = yield session_manager.create_session(user_id)
            session['state'] = 'new'

        pfunc = getattr(self, 'process_message_%s' % (session['state'],))
        try:
            session = yield pfunc(msg, config, session)
            yield self.handle_session_result(session_manager, user_id, session)
        except:
            # Uncomment to raise instead of logging (useful for tests)
            # raise
            log.err()
            self.fire_metric('ussd_session_error')
            self.reply_to(msg, config.msg_error, False)
            yield session_manager.clear_session(user_id)

    def process_message_new(self, msg, config, session):
        """ Input:  User dialed USSD magic number.
            Output: Search string prompt."""
        self.log_action(msg, 'start')
        self.fire_metric('ussd_session_start')
        self.reply_to(msg, config.msg_prompt, True)
        session['state'] = 'searching'
        return session

    @inlineCallbacks
    def process_message_searching(self, msg, config, session):
        """ Input:  User gives a search query.
            Output: List of search results (titles)."""
        self.fire_metric('ussd_session_search')
        query = msg['content'].strip()

        results = yield self.get_wikipedia_api(config).search(query)
        if results:
            count, msgcontent = self.make_options(config, results)
            session['results'] = json.dumps(results[:count])
            self.reply_to(msg, msgcontent, True)
            session['state'] = 'sections'
        else:
            count = 0
            self.fire_metric('ussd_session_search.no_results')
            self.reply_to(msg, config.msg_no_results % query, False)
            session['state'] = None
        self.log_action(msg, 'titles', found=len(results), shown=count)
        returnValue(session)

    def select_option(self, config, options, msg, metric_prefix=None):
        response = msg['content'].strip()

        if response.isdigit():
            try:
                index = int(response) - 1
                result = options[index]
                self.fire_metric(metric_prefix, index + 1)
                return (result, index)
            except (KeyError, IndexError):
                pass
        self.fire_metric(metric_prefix, 'invalid')
        self.reply_to(msg, config.msg_invalid_section, False)
        return (None, None)

    @inlineCallbacks
    def process_message_sections(self, msg, config, session):
        """ Input:  User selects the search result.
            Output: List of article section titles"""
        self.fire_metric('ussd_session_results')
        selection, index = self.select_option(
            config,
            json.loads(session['results']), msg,
            metric_prefix='ussd_session_results')
        if not selection:
            session['state'] = None
            self.log_action(msg, 'section-invalid')
            returnValue(session)

        session['page'] = json.dumps(selection)
        extract = yield self.get_extract(config, selection)
        results = [selection] + [s.title for s in extract.sections[1:]]
        count, msgcontent = self.make_options(config, [r for r in results])
        session['results'] = json.dumps(results[:count])
        self.reply_to(msg, msgcontent, True)
        session['state'] = 'content'
        self.log_action(msg, 'section', title=selection,
                        found=len(extract.sections), shown=count)
        returnValue(session)

    def normalize_content(self, config, content):
        text = content
        if config.transliterate_unicode:
            text = transliterate_unicode(text)
        sms = text
        sms = normalize_whitespace(text)
        if config.minimize_text:
            sms = minimize_unicode(sms)
        return (text, sms)

    @inlineCallbacks
    def process_message_content(self, msg, config, session):
        """ Input:  User selects the article section.
            Output: Section content -> USSD + SMS"""
        self.fire_metric('ussd_session_sections')
        sections = json.loads(session['results'])
        selection, index = self.select_option(
            config, sections, msg, metric_prefix='ussd_session_sections')
        if not selection:
            session['state'] = None
            self.log_action(msg, 'content-invalid')
            returnValue(session)
        page = json.loads(session['page'])
        extract = yield self.get_extract(config, page)
        content = extract.sections[index].full_text()
        ussd_text, sms_text = self.normalize_content(config, content)
        session['sms_content'] = sms_text
        session['sms_offset'] = 0
        ussd_cont = self.get_ussd_formatter(config).format(
            ussd_text, config.msg_ussd_suffix)
        self.fire_metric('ussd_session_content')
        self.reply_to(msg, ussd_cont, False)
        self.log_action(
            msg, 'ussdcontent', section=selection,
            contentLen=len(content), smsLen=len(sms_text),
            content=ussd_cont)
        if config.send_sms_content:
            session = yield self.send_sms_content(msg, config, session)
        if not config.send_more_sms_content:
            session['state'] = None
        else:
            session['state'] = 'more'
        returnValue(session)

    @inlineCallbacks
    def send_sms_content(self, msg, config, session):
        content_len, sms_content = self.get_sms_formatter(config).format_more(
            session['sms_content'], session['sms_offset'],
            config.msg_more_content_suffix, config.msg_no_more_content_suffix)
        session['sms_offset'] = session['sms_offset'] + content_len + 1
        if session['sms_offset'] >= len(session['sms_content']):
            session['state'] = None

        if msg.get_routing_endpoint() == 'default':
            # We're sending this message in response to a USSD session.
            yield self.send_sms_non_reply(msg, config, sms_content)
        elif msg.get_routing_endpoint() == 'sms_content':
            # We're sending this message in response to a 'more content' SMS.
            yield self.reply_to(msg, sms_content)
        self.log_action(msg, 'smscontent', content=sms_content,
                        more=(session['state'] is not None))

        returnValue(session)

    def send_sms_non_reply(self, msg, config, sms_content):
        return self.send_to(
            msg['from_addr'], sms_content, transport_type='sms',
            endpoint='sms_content')

    @inlineCallbacks
    def consume_content_sms_message(self, msg):
        # log.msg("Received SMS: %s" % (msg.payload,))
        config = yield self.get_config(msg)

        # This is to exclude some spurious messages we might receive.
        if msg['content'] is None:
            self.log_action(msg, 'more-no-content')
            return

        user_id = msg.user()

        session_manager = self.get_session_manager(config)

        session = yield self.load_session(session_manager, user_id)
        self.fire_metric('sms_more_content_reply')
        if not session:
            self.log_action(msg, 'more-no-session')
            # TODO: Reply with error?
            self.fire_metric('sms_more_content_reply.no_content')
            return

        if session['state'] != 'more':
            self.log_action(msg, 'more-wrong-session-state')
            return

        # FIXME: This is a stopgap until we can figure out why wy sometimes get
        #        strings instead of integers here.
        raw_more_messages = session.get('more_messages', 0)
        if int(raw_more_messages) != raw_more_messages:
            log.warning("Found non-integer 'more_messages': %r" % (
                raw_more_messages,))
            raw_more_messages = int(raw_more_messages)
        more_messages = raw_more_messages + 1
        session['more_messages'] = more_messages
        if more_messages > 9:
            more_messages = 'extra'
        self.fire_metric('sms_more_content_reply', more_messages)

        try:
            session = yield self.send_sms_content(msg, config, session)
            yield self.handle_session_result(session_manager, user_id, session)
        except:
            log.err()
            # TODO: Reply with error?
            yield session_manager.clear_session(user_id)
