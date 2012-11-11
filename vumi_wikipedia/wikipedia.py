# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia -*-

import json

from twisted.internet.defer import inlineCallbacks, returnValue
from vumi import log
from vumi.application import ApplicationWorker
from vumi.persist.txredis_manager import TxRedisManager
from vumi.components.session import SessionManager
from vumi.message import TransportUserMessage, TransportEvent
from vumi.blinkenlights.metrics import MetricManager, Count

from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract
from vumi_wikipedia.text_manglers import normalize_whitespace, ContentFormatter


def mkmenu(options, prefix, start=1):
    return prefix + '\n'.join(
        ['%s. %s' % (idx, opt) for idx, opt in enumerate(options, start)])


class WikipediaWorker(ApplicationWorker):
    """Access MediaWiki content over SMS/USSD.

    This application provides an interface to Wikipedia (or ant other MediaWiki
    installation) over SMS and/or USSD.

    When a USSD transport is provided, searching and article selection can be
    done over USSD and article content is delivered over SMS.

    When SMS search and menu transports are provided, searching and article
    selection can be done over SMS.

    At least one of the above mechanisms must be used, but using them together
    in the same worker instance is also possible.

    TODO: Document which transport parameters need to be specified together,
    etc.

    Config parameters
    -----------------

    :param str ussd_transport:
        If set, this specifies the USSD transport for searching Wikipedia.
        (optional)

    :param str sms_search_transport:
        If set, this specifies the SMS transport for receiving search queries.
        No outbound messages will be sent over this transport.
        (optional, but must be specified together with sms_menu_transport)

    :param str sms_menu_transport:
        If set, this specifies the SMS transport to use for displaying menu
        options and receiving selection messages.
        (optional, but must be specified together with sms_search_transport)

    :param str sms_content_transport:
        SMS transport to use for sending article text and receiving requests
        for more content.

    :param str api_url:
        Alternate API URL to use. This can be any MediaWiki deployment,
        although certain assumptions are made about the structure of articles
        that may not be valid outside of Wikipedia.
        (optional, defaults to the English Wikipedia API)

    :param bool accept_gzip:
        If `True`, the HTTP client will request gzipped responses. This is
        generally beneficial, although it requires Twisted 11.1 or later.
        (optional, defaults to `False`)

    :param str user_agent:
        Override `User-Agent` header on API requests.
        (optional)

    :param int max_session_length:
        Lifetime of the search session in seconds.
        (optional, defaults to 10 minutes.)

    :param int content_cache_time:
        Lifetime of cached article content in seconds. If set to 0, disables
        content caching.
        (optional, defaults to 0)

    :param int max_ussd_content_length:
        Maximum character length of ASCII USSD content.
        (optional, defaults to 180)

    :param int max_ussd_unicode_length:
        Maximum character length of unicode USSD content.
        (optional, defaults to 90)

    :param int max_sms_content_length:
        Maximum character length of ASCII SMS content. Recommended values are
        160 for a single message or multiples of 150 for a multipart message.
        (optional, defaults to 160)

    :param int max_sms_unicode_length:
        Maximum character length of unicode SMS content. Recommended values are
        70 for a single message or multiples of 60 for a multipart message.
        (optional, defaults to 70)

    :param int sentence_break_threshold:
        If a sentence break is found within this many characters of the end of
        the truncated message, truncate at the sentence break instead of a word
        break.
        (optional, defaults to 10)

    :param str more_content_postfix:
        Postfix for SMS content that can be continued. If requests for more
        content are unsupported, this should be set to an empty string.
        (optional, defaults to ' (reply for more)')

    :param str no_more_content_postfix:
        Postfix for SMS content that is complete. If requests for more
        content are unsupported, this should be set to an empty string.
        (optional, defaults to ' (end of section)')

    :param str metrics_prefix:
        Prefix for metrics names. If unset, no metrics will be collected.
        (optional)
    """

    MAX_SESSION_LENGTH = 600
    CONTENT_CACHE_TIME = 0

    MAX_USSD_CONTENT_LENGTH = 180
    MAX_USSD_UNICODE_LENGTH = 90
    MAX_SMS_CONTENT_LENGTH = 160
    MAX_SMS_UNICODE_LENGTH = 70
    SENTENCE_BREAK_THRESHOLD = 10

    MORE_CONTENT_POSTFIX = u' (reply for more)'
    NO_MORE_CONTENT_POSTFIX = u' (end of section)'

    # Some message constants. These need to be translated at some point.
    MSG_REQUEST_ERROR = (
        'Sorry, there was an error processing your request. '
        'Please try again later.')
    MSG_SEARCH_PROMPT = 'What would you like to search Wikipedia for?'
    MSG_NO_RESULTS = 'Sorry, no Wikipedia results for "%s".'
    MSG_INVALID_SELECTION = (
        'Sorry, invalid selection. Please restart and try again.')

    def _opt_config(self, name):
        return self.config.get(name, None)

    # We don't use the standard `transport_name` config here, because our
    # transport setup is more complicated than that.
    # TODO: Maybe find a better way to do this?

    def _validate_config(self):
        # Override the default config stuff, because we don't use
        # `transport_name`
        if 'transport_name' in self.config:
            log.warning("This application is strange and doesn't use a "
                        "'transport_name' config.")
        return self.validate_config()

    def validate_config(self):
        # Transport names
        self.ussd_transport = self._opt_config('ussd_transport')
        self.sms_search_transport = self._opt_config('sms_search_transport')
        self.sms_menu_transport = self._opt_config('sms_menu_transport')
        self.sms_content_transport = self._opt_config('sms_content_transport')
        # TODO: Validate transport combinations.

        self.api_url = self._opt_config('api_url')
        self.accept_gzip = self._opt_config('accept_gzip')
        self.user_agent = self._opt_config('user_agent')

        self.max_session_length = self.config.get(
            'max_session_length', self.MAX_SESSION_LENGTH)
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

        self.more_content_postfix = self.config.get(
                'more_content_postfix', self.MORE_CONTENT_POSTFIX)
        self.no_more_content_postfix = self.config.get(
                'no_more_content_postfix', self.NO_MORE_CONTENT_POSTFIX)

        self.metrics_prefix = self.config.get('metrics_prefix')

    @inlineCallbacks
    def _setup_transport_publisher(self):
        # We override this to set up our own complicated transports
        if self.ussd_transport is not None:
            # For USSD
            self.ussd_transport_publisher = yield self.publish_to(
                '%s.outbound' % (self.ussd_transport,))

        if self.sms_search_transport is not None:
            # For SMS
            self.sms_search_transport_publisher = yield self.publish_to(
                '%s.outbound' % (self.sms_search_transport,))
            self.sms_menu_transport_publisher = yield self.publish_to(
                '%s.outbound' % (self.sms_menu_transport,))

        # For both
        self.sms_content_transport_publisher = yield self.publish_to(
            '%s.outbound' % (self.sms_content_transport,))

    @inlineCallbacks
    def _setup_transport_consumer(self):
        # We override this to set up our own complicated transports
        if self.ussd_transport is not None:
            # For USSD
            self.ussd_transport_consumer = yield self.consume(
                '%s.inbound' % (self.ussd_transport,),
                self.consume_ussd_message,
                message_class=TransportUserMessage)
            self._consumers.append(self.ussd_transport_consumer)

        if self.sms_search_transport is not None:
            # For SMS
            self.sms_search_transport_consumer = yield self.consume(
                '%s.inbound' % (self.sms_search_transport,),
                self.consume_sms_search_message,
                message_class=TransportUserMessage)
            self._consumers.append(self.sms_search_transport_consumer)
            self.sms_menu_transport_consumer = yield self.consume(
                '%s.inbound' % (self.sms_menu_transport,),
                self.consume_sms_menu_message,
                message_class=TransportUserMessage)
            self._consumers.append(self.sms_menu_transport_consumer)

        # For both
        self.sms_content_transport_consumer = yield self.consume(
            '%s.inbound' % (self.sms_content_transport,),
            self.consume_sms_content_message,
            message_class=TransportUserMessage)
        self._consumers.append(self.sms_content_transport_consumer)

    @inlineCallbacks
    def _setup_event_consumer(self):
        # We override this to set up our own complicated transports
        if self.ussd_transport is not None:
            # For USSD
            self.ussd_transport_event_consumer = yield self.consume(
                '%s.inbound' % (self.ussd_transport,),
                self.consume_ussd_event,
                message_class=TransportEvent)
            self._consumers.append(self.ussd_transport_event_consumer)

        if self.sms_search_transport is not None:
            # For SMS
            self.sms_search_transport_event_consumer = yield self.consume(
                '%s.inbound' % (self.sms_search_transport,),
                self.consume_sms_search_event,
                message_class=TransportEvent)
            self._consumers.append(self.sms_search_transport_event_consumer)
            self.sms_menu_transport_event_consumer = yield self.consume(
                '%s.inbound' % (self.sms_menu_transport,),
                self.consume_sms_menu_event,
                message_class=TransportEvent)
            self._consumers.append(self.sms_menu_transport_event_consumer)

        # For both
        self.sms_content_transport_event_consumer = yield self.consume(
            '%s.inbound' % (self.sms_content_transport,),
            self.consume_sms_content_event,
            message_class=TransportEvent)
        self._consumers.append(self.sms_content_transport_event_consumer)

    @inlineCallbacks
    def setup_application(self):
        yield self._setup_metrics()
        redis = yield TxRedisManager.from_config(
            self.config.get('redis_manager', {}))
        redis = redis.sub_manager(self.config['worker_name'])

        self.extract_redis = redis.sub_manager('extracts')

        self.session_manager = SessionManager(
            redis.sub_manager('session'),
            max_session_length=self.max_session_length)

        self.wikipedia = WikipediaAPI(
            self.api_url, self.accept_gzip, self.user_agent)

        self.ussd_formatter = ContentFormatter(
            self.max_ussd_content_length, self.max_ussd_unicode_length,
            sentence_break_threshold=0)

        self.sms_formatter = ContentFormatter(
            self.max_sms_content_length, self.max_sms_unicode_length,
            sentence_break_threshold=self.sentence_break_threshold)

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

    def consume_sms_search_message(self, msg):
        raise NotImplementedError()

    def consume_sms_menu_message(self, msg):
        raise NotImplementedError()

    def consume_sms_content_message(self, msg):
        return self.consume_sms_message(msg)

    def consume_ussd_event(self, msg):
        raise NotImplementedError()

    def consume_sms_search_event(self, msg):
        raise NotImplementedError()

    def consume_sms_menu_event(self, msg):
        raise NotImplementedError()

    def consume_sms_content_event(self, msg):
        raise NotImplementedError()

    def reply_via(self, channel, original_message, content,
                  continue_session=True, **kws):
        """Send a reply over the appropriate transport based on the channel.

        :param str channel:
            Channel must be one of `menu` or `content`.
        """
        # TODO: Figure out a more generic way to do this.
        reply = original_message.reply(content, continue_session, **kws)

        if channel == 'content':
            # We only have one SMS content publisher.
            reply['transport_name'] = self.sms_content_transport
            return self.sms_content_transport_publisher.publish_message(reply)

        if original_message['transport_name'] == self.ussd_transport:
            # All replies to USSD messages (except SMS content, which we've
            # already handled) must go over the USSD transport. We already have
            # the right transport_name set here.
            return self.ussd_transport_publisher.publish_message(reply)

        if channel == 'menu':
            # We've handled all USSD messages now, so we just have to choose
            # the right publisher and set the transport_name in the message
            # appropriately.
            reply['transport_name'] = self.sms_menu_transport
            return self.sms_menu_transport_publisher.publish_message(reply)

        raise Exception("I don't know about channel %r!" % (channel,))

    @inlineCallbacks
    def consume_ussd_message(self, msg):
        log.msg("Received: %s" % (msg.payload,))
        user_id = msg.user()
        session_event = self._message_session_event(msg)
        session = yield self.load_session(user_id)

        if session_event == 'close':
            if (session and session['state'] != 'more'):
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
            self.reply_via('menu', msg, self.MSG_REQUEST_ERROR, False)
            yield self.session_manager.clear_session(user_id)

    def process_message_new(self, msg, session):
        self.fire_metric('ussd_session_start')
        self.reply_via('menu', msg, self.MSG_SEARCH_PROMPT, True)
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
            self.reply_via('menu', msg, msgcontent, True)
            session['state'] = 'sections'
        else:
            self.fire_metric('ussd_session_search.no_results')
            self.reply_via('menu', msg, self.MSG_NO_RESULTS % (query,), False)
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
        self.reply_via('menu', msg, self.MSG_INVALID_SELECTION, False)
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
        self.reply_via('menu', msg, msgcontent, True)
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
        if msg['transport_name'] == self.ussd_transport:
            ussd_cont = self.ussd_formatter.format(
                content, '\n(Full content sent by SMS.)')
            self.fire_metric('ussd_session_content')
            self.reply_via('menu', msg, ussd_cont, False)
        session = yield self.send_sms_content(msg, session)
        session['state'] = 'more'
        returnValue(session)

    def send_sms_content(self, msg, session):
        # TODO: Make this less hacky.
        content_len, sms_content = self.sms_formatter.format_more(
            session['sms_content'], session['sms_offset'],
            self.more_content_postfix, self.no_more_content_postfix)
        session['sms_offset'] = session['sms_offset'] + content_len + 1
        if session['sms_offset'] >= len(session['sms_content']):
            session['state'] = None

        bmsg = msg.reply(sms_content)
        bmsg['transport_name'] = self.sms_content_transport
        bmsg['transport_type'] = 'sms'
        self.sms_content_transport_publisher.publish_message(bmsg)

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
