# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia -*-

import json

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import log
from vumi.application import ApplicationWorker
from vumi.persist.txredis_manager import TxRedisManager
from vumi.components.session import SessionManager

from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract
from vumi_wikipedia.text_manglers import (
    normalize_whitespace, truncate_sms, truncate_sms_with_postfix)


class WikipediaUSSDFlow(object):
    def __init__(self, worker, session):
        self.session = session


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

    max_ussd_session_length : int, optional
        Lifetime of USSD session in seconds. Defaults to 3 minutes.

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
    """

    MAX_USSD_SESSION_LENGTH = 3 * 60
    CONTENT_CACHE_TIME = 3600

    MAX_USSD_CONTENT_LENGTH = 160
    MAX_USSD_UNICODE_LENGTH = 70
    MAX_SMS_CONTENT_LENGTH = 160
    MAX_SMS_UNICODE_LENGTH = 70

    def _opt_config(self, name):
        return self.config.get(name, None)

    def validate_config(self):
        self.sms_transport = self._opt_config('sms_transport')
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

    @inlineCallbacks
    def setup_application(self):
        redis = yield TxRedisManager.from_config(
            self.config.get('redis_manager', {}))
        redis = redis.sub_manager(self.config['worker_name'])

        self.extract_redis = redis.sub_manager('extracts')

        self.session_manager = SessionManager(
            redis.sub_manager('session'),
            max_session_length=self.max_ussd_session_length)

        self.wikipedia = WikipediaAPI(
            self.api_url, self.accept_gzip, self.user_agent)

    def teardown_application(self):
        return self.session_manager.stop()

    def make_options(self, options, prefix='', start=1):
        """
        Turn a list of results into an enumerated multiple choice list
        """
        joined = mkmenu(options, prefix,
         start)
        while len(joined) > self.max_ussd_content_length:
            if not options:
                break
            options = options[:-1]
            joined = mkmenu(options, prefix, start)

        return len(options), joined[:self.max_ussd_content_length]

    @inlineCallbacks
    def get_extract(self, title):
        key = self.wikipedia.url + ':' + title
        data = yield self.extract_redis.get(key)
        if data is None:
            extract = yield self.wikipedia.get_extract(title)
            data = json.dumps(extract.sections)
            # We do this in two steps because our redis clients disagree on
            # what SETEX should look like.
            yield self.extract_redis.set(key, data)
            yield self.extract_redis.expire(key, self.content_cache_time)
        else:
            extract = ArticleExtract(json.loads(data))
        returnValue(extract)

    @inlineCallbacks
    def consume_user_message(self, msg):
        log.msg("Received: %s" % (msg.payload,))
        user_id = msg.user()
        session = yield self.session_manager.load_session(user_id)
        if (not session) or (msg['content'] is None):
            session = yield self.session_manager.create_session(user_id)
            session['state'] = 'new'

        pfunc = getattr(self, 'process_message_%s' % (session['state'],))
        try:
            session = yield pfunc(msg, session)
            if session['state'] is not None:
                yield self.session_manager.save_session(user_id, session)
            else:
                yield self.session_manager.clear_session(user_id)
        except:
            log.err()
            self.reply_to(
                msg, 'Sorry, there was an error processing your request. '
                'Please try again later.', False)
            yield self.session_manager.clear_session(user_id)

    def process_message_new(self, msg, session):
        self.reply_to(
            msg, "What would you like to search Wikipedia for?", True)
        session['state'] = 'searching'
        return session

    @inlineCallbacks
    def process_message_searching(self, msg, session):
        query = msg['content'].strip()

        results = yield self.wikipedia.search(query)
        if results:
            count, msgcontent = self.make_options(results)
            session['results'] = json.dumps(results[:count])
            self.reply_to(msg, msgcontent, True)
            session['state'] = 'sections'
        else:
            self.reply_to(
                msg, 'Sorry, no Wikipedia results for %s' % query, False)
            session['state'] = None
        returnValue(session)

    def select_option(self, options, msg):
        response = msg['content'].strip()

        if response.isdigit():
            try:
                return options[int(response) - 1]
            except (KeyError, IndexError):
                pass
        self.reply_to(msg,
                      'Sorry, invalid selection. Please restart and try again',
                      False)
        return None

    @inlineCallbacks
    def process_message_sections(self, msg, session):
        selection = self.select_option(json.loads(session['results']), msg)
        if not selection:
            session['state'] = None
            returnValue(session)

        session['page'] = json.dumps(selection)
        extract = yield self.get_extract(selection)
        results = extract.get_section_titles()  # TODO:
        results = [selection] + results
        count, msgcontent = self.make_options([r for r in results])
        session['results'] = json.dumps(results[:count])
        self.reply_to(msg, msgcontent, True)
        session['state'] = 'content'
        returnValue(session)

    @inlineCallbacks
    def process_message_content(self, msg, session):
        sections = json.loads(session['results'])
        selection = self.select_option(sections, msg)
        if not selection:
            session['state'] = None
            returnValue(session)
        page = json.loads(session['page'])
        extract = yield self.get_extract(page)
        content = extract.sections[int(msg['content'].strip()) - 1]['text']
        ussd_cont = truncate_sms_with_postfix(
            content, '\n(Full content sent by SMS.)',
            self.max_ussd_content_length, self.max_ussd_unicode_length)
        self.reply_to(msg, ussd_cont, False)
        if self.sms_transport:
            sms_content = normalize_whitespace(content)
            # TODO: Decide if we want this.
            sms_content = truncate_sms(
                sms_content,
                self.max_sms_content_length, self.max_sms_unicode_length)
            bmsg = msg.reply(sms_content)
            bmsg['transport_name'] = self.sms_transport
            if self.override_sms_address:
                bmsg['to_addr'] = self.override_sms_address
            self.transport_publisher.publish_message(
                bmsg, routing_key='%s.outbound' % (self.sms_transport,))
        session['state'] = None
        returnValue(session)
