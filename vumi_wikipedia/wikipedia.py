# -*- test-case-name: vumi.demos.tests.test_wikipedia -*-

from twisted.internet.defer import inlineCallbacks, returnValue
from vumi.application import ApplicationWorker, SessionManager
from vumi.utils import http_request, get_deploy_int
from urllib import urlencode
import json
import redis


class WikipediaAPI(object):
    """
    Small Wikipedia API client library.
    """

    URL = 'http://en.wikipedia.org/w/api.php'

    # The MediaWiki API docs request that clients use gzip encoding to reduce
    # network traffic. However, Twisted only supports this easily from 11.1.
    GZIP = False

    @inlineCallbacks
    def _make_call(self, params):
        params.setdefault('format', 'json')
        url = '%s?%s' % (self.URL, urlencode(params))
        headers = {
            'User-Agent': 'Vumi HTTP Request',
            }
        if self.GZIP:
            headers['Accept-Encoding'] = 'gzip'
        response = yield http_request(url, '', headers, method='GET')
        returnValue(json.loads(response))

    @inlineCallbacks
    def search(self, query, limit=9):
        """
        Perform a query and returns a list of results matching the query.

        Parameters
        ----------
        query : str
            The search term.
        limit : int, optional
            How many results to get back, defaults to 9.
        """
        response = yield self._make_call({
                'action': 'query',
                'list': 'search',
                'srsearch': query.encode('utf-8'),
                'srlimit': str(limit),
                })
        results = [r['title'] for r in response['query']['search']]
        returnValue(results)

    @inlineCallbacks
    def get_sections(self, page_name):
        """
        Return a list of top-level section headings for a page.

        Parameters
        ----------
        page_name : str
            The name of the page to query.
        """
        response = yield self._make_call({
                'action': 'parse',
                'page': page_name.encode('utf-8'),
                'prop': 'sections',
                'redirects': '1',
                })

        sections = [sec['line'] for sec in response['parse']['sections']
                    if sec['toclevel'] == 1]
        returnValue(sections)

    @inlineCallbacks
    def get_content(self, page_name, section_number):
        """
        Return the content of a section of a page.

        Parameters
        ----------
        page_name : str
            The name of the page to query.
        section_number : int
            The section number to retrieve.
        """
        response = yield self._make_call({
                'action': 'parse',
                'page': page_name.encode('utf-8'),
                'prop': 'wikitext',
                'section': str(section_number),
                'redirects': '1',
                })

        text = response['parse']['wikitext']['*']
        returnValue(text[:500])


def image(item, element):
    el = item.find(element)
    return getattr(el, 'attrib', {})


def pretty_print_results(results, start=1):
    """
    Turn a list of results into an enumerate multiple choice list
    """
    return '\n'.join(['%s. %s' % (idx, result['text'])
                      for idx, result in enumerate(results, start)])


def format_options(options, start=1):
    """
    Turn a list of results into an enumerate multiple choice list
    """
    return '\n'.join(['%s. %s' % (idx, opt)
                      for idx, opt in enumerate(options, start)])


class WikipediaUSSDFlow(object):
    def __init__(self, worker, session):
        self.session = session


class WikipediaWorker(ApplicationWorker):

    MAX_SESSION_LENGTH = 3 * 60

    @inlineCallbacks
    def startWorker(self):
        self.sms_transport = self.config.get('sms_transport', None)
        self.override_sms_address = self.config.get('override_sms_address',
                                                    None)
        db = get_deploy_int(self._amqp_client.vhost)
        self.r_server = redis.Redis("localhost", db=db)
        self.session_manager = SessionManager(self.r_server,
            "%(worker_name)s:%(transport_name)s" % self.config,
            max_session_length=self.MAX_SESSION_LENGTH)

        yield super(WikipediaWorker, self).startWorker()

    @inlineCallbacks
    def stopWorker(self):
        yield self.session_manager.stop()
        yield super(WikipediaWorker, self).stopWorker()

    @inlineCallbacks
    def consume_user_message(self, msg):
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

        results = yield WikipediaAPI().search(query)
        if results:
            session['results'] = json.dumps(results)
            self.reply_to(msg, format_options(results), True)
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

        session['page'] = selection
        results = yield WikipediaAPI().get_sections(selection)
        results = [selection] + results
        session['results'] = json.dumps(results)
        self.reply_to(msg, format_options(results), True)
        session['state'] = 'content'
        returnValue(session)

    @inlineCallbacks
    def process_message_content(self, msg, session):
        selection = self.select_option(json.loads(session['results']), msg)
        if not selection:
            session['state'] = None
            returnValue(session)
        content = yield WikipediaAPI().get_content(
            session['page'], int(msg['content'].strip()) - 1)
        ussd_cont = "%s...\n(Full content sent by SMS.)" % (content[:100],)
        self.reply_to(msg, ussd_cont, False)
        if self.sms_transport:
            bmsg = msg.reply(content[:250])
            bmsg['transport_name'] = self.sms_transport
            if self.override_sms_address:
                bmsg['to_addr'] = self.override_sms_address
            self.transport_publisher.publish_message(
                bmsg, routing_key='%s.outbound' % (self.sms_transport,))
        session['state'] = None
        returnValue(session)
