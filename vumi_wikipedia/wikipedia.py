# -*- test-case-name: vumi_wikipedia.tests.test_wikipedia -*-
import json
import redis
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import log

from vumi.application import ApplicationWorker, SessionManager

from vumi_wikipedia.wikipedia_api import WikipediaAPI, ArticleExtract

from vumi_wikipedia.text_manglers import normalize_whitespace


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

    content_type : str, optional
        If set to `wikitext` (the default), raw WikiText content will be
        returned. If set to `text`, HTML content will be processed into plain
        text and returned. While the `text` content type is probably better,
        the processing may not be sufficiently robust in the face of adversity.
    """

    MAX_SESSION_LENGTH = 3 * 60
    MAX_CONTENT_LENGTH = 160
    CONTENT_TYPE = 'wikitext'

    @inlineCallbacks
    def startWorker(self):
        self.sms_transport = self.config.get('sms_transport', None)
        self.override_sms_address = self.config.get('override_sms_address',
                                                    None)
        self.r_config = self.config.get('redis_config', {})
        self.r_server = redis.Redis(**self.r_config)
        self.session_manager = SessionManager(self.r_server,
            "%(worker_name)s:%(transport_name)s" % self.config,
            max_session_length=self.MAX_SESSION_LENGTH)

        self.wikipedia = WikipediaAPI(
            self.config.get('api_url', None),
            self.config.get('accept_gzip', None))

        self.content_type = self.config.get('content_type', self.CONTENT_TYPE)
        yield super(WikipediaWorker, self).startWorker()

    @inlineCallbacks
    def stopWorker(self):
        yield self.session_manager.stop()
        yield super(WikipediaWorker, self).stopWorker()

    def make_options(self, options, prefix='', start=1):
        """
        Turn a list of results into an enumerated multiple choice list
        """
        joined = mkmenu(options, prefix,
         start)
        while len(joined) > self.MAX_CONTENT_LENGTH:
            if not options:
                break
            options = options[:-1]
            joined = mkmenu(options, prefix, start)

        return len(options), joined[:self.MAX_CONTENT_LENGTH]

    @inlineCallbacks
    def get_extract(self, title):
        key = self.wikipedia.url + ':' + title
        data = self.r_server.get(key)
        if data == None:
            extract = yield self.wikipedia.get_extract(title)
            data = json.dumps(extract.sections)
            self.r_server.setex(key, data, 3600)
        else:
            extract = ArticleExtract(json.loads(data))
        returnValue(extract)

    @inlineCallbacks
    def consume_user_message(self, msg):
        log.msg("Received: %s" % (msg.payload,))
        user_id = msg.user()
        session = self.session_manager.load_session(user_id)
        if (not session) or (msg['content'] is None):
            session = self.session_manager.create_session(user_id)
            session['state'] = 'new'

        pfunc = getattr(self, 'process_message_%s' % (session['state'],))
        session = yield pfunc(msg, session)
        if session['state'] is not None:
            self.session_manager.save_session(user_id, session)
        else:
            self.session_manager.clear_session(user_id)

    def process_message_new(self, msg, session):
        self.reply_to(msg, "What would you like to search Wikipedia for?",
            True)
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
            self.reply_to(msg, 'Sorry, no Wikipedia results for %s' % query,
                          False)
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
        ussd_cont = "%s...\n(Full content sent by SMS.)" % (content[:100],)
        self.reply_to(msg, ussd_cont, False)
        if self.sms_transport:
            sms_content = normalize_whitespace(content)[:250]
            sms_content = content[:250]  # TODO: Decide if we want this.
            bmsg = msg.reply(sms_content)
            bmsg['transport_name'] = self.sms_transport
            if self.override_sms_address:
                bmsg['to_addr'] = self.override_sms_address
            self.transport_publisher.publish_message(
                bmsg, routing_key='%s.outbound' % (self.sms_transport,))
        session['state'] = None
        returnValue(session)
