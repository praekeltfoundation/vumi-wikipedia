"""Tests for vumi.demos.wikipedia."""

import json
from functools import wraps
from pkg_resources import resource_stream

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.protocol import Protocol, Factory
from twisted.trial.unittest import TestCase

from vumi.tests.fake_amqp import FakeAMQPBroker
from vumi.tests.utils import get_stubbed_worker
from vumi.message import TransportUserMessage

from vumi_wikipedia.wikipedia import WikipediaAPI, WikipediaWorker


WIKIPEDIA_RESPONSES = json.load(
    resource_stream(__name__, 'wikipedia_responses.json'))


class FakeHTTP(Protocol):
    def dataReceived(self, data):
        request_line, body = self.parse_request(data)
        response = self.handle_request(request_line, body)
        self.transport.write(response.encode('utf-8'))
        self.transport.loseConnection()

    def parse_request(self, data):
        headers, _, body = data.partition('\r\n\r\n')
        headers = headers.splitlines()
        request_line = headers.pop(0).rsplit(' ', 1)[0]
        return request_line, body

    def build_response(self, response_data):
        lines = ["HTTP/1.1 %s" % (response_data['response_code'],)]
        lines.extend(['', json.dumps(response_data['response_body'])])
        return '\r\n'.join(lines)

    def handle_request(self, request_line, body):
        response_data = self.factory.response_data.get(request_line)
        if not response_data:
            self.factory.testcase.fail(
                "Unexpected request: %s" % (request_line,))
        self.factory.testcase.assertEqual(response_data["request_body"], body)
        return self.build_response(response_data)


class FakeHTTPTestCaseMixin(object):
    @inlineCallbacks
    def start_webserver(self, response_data):
        factory = Factory()
        factory.protocol = FakeHTTP
        factory.response_data = response_data
        factory.testcase = self
        self.webserver = yield reactor.listenTCP(0, factory)
        addr = self.webserver.getHost()
        self.url = "http://%s:%s/" % (addr.host, addr.port)

    def stop_webserver(self):
        return self.webserver.loseConnection()


def debug_api_call(func):
    @wraps(func)
    def wrapped_test(self):
        self.wikipedia.PRINT_DEBUG = True
        self.wikipedia.url = self.wikipedia.URL
        return func(self)
    return wrapped_test


class WikipediaAPITestCase(TestCase, FakeHTTPTestCaseMixin):
    timeout = 10

    @inlineCallbacks
    def setUp(self):
        yield self.start_webserver(WIKIPEDIA_RESPONSES)
        self.wikipedia = WikipediaAPI(self.url, False)

    def tearDown(self):
        return self.stop_webserver()

    def assert_api_result(self, api_result_d, expected):
        return api_result_d.addCallback(self.assertEqual, expected)

    @inlineCallbacks
    def test_search_success(self):
        yield self.assert_api_result(
            self.wikipedia.search('wikipedia', limit=3),
            [u'Wikipedia', u'Wikip\xe9dia', u'Main Page'])
        # And again with a different request and result limit
        yield self.assert_api_result(
            self.wikipedia.search('vumi', limit=2),
            [u'Arambagh Utsab', u'Vulpia microstachys'])

    @inlineCallbacks
    def test_search_no_results(self):
        yield self.assert_api_result(
            self.wikipedia.search('ncdkiuagdqpowebjkcs', limit=3), [])

    @inlineCallbacks
    def test_get_sections_success(self):
        yield self.assert_api_result(
            self.wikipedia.get_sections('Triassic'), [
                (u'Dating and subdivisions', u'1'),
                (u'Paleogeography', u'2'),
                (u'Climate', u'4'),
                (u'Life', u'5'),
                (u'Coal', u'9'),
                (u'Lagerst\xe4tten', u'10'),
                (u'Late Triassic extinction event', u'11'),
                (u'See also', u'12'),
                (u'Notes', u'13'),
                (u'References', u'14'),
                (u'External links', u'15'),
                ])

    @inlineCallbacks
    def test_get_sections_none(self):
        yield self.assert_api_result(
            self.wikipedia.get_sections('Martin Lake'), [])

    @inlineCallbacks
    def test_get_content(self):
        yield self.assert_api_result(
            self.wikipedia.get_content('Dominion of New Zealand', 0),
            u"The '''Dominion of New Zealand''' is the former name of the "
            u"[[Realm of New Zealand]].\n\n[[Image:Dominion Day New "
            u"Zealand.jpg|thumb|250px|right|[[William Plunket, 5th Baron "
            u"Plunket|Lord Plunket]] declaring New Zealand a Dominion, 1907]]"
            u"\nOriginally administered from [[New South Wales]], New Zealand "
            u"became a direct British colony in 1841 and received a large "
            u"measure of [[self-government]] following the [[New Zealand "
            u"Constitution Act 1852]]. New Zealand chose not to take part in "
            u"[[Australian Federati")

    @inlineCallbacks
    def test_get_content_html(self):
        yield self.assert_api_result(
            self.wikipedia.get_content('Dominion of New Zealand', 0,
                                       content_type='text'),
            u"The Dominion of New Zealand is the former name of the Realm of "
            u"New Zealand.\nOriginally administered from New South Wales, "
            u"New Zealand became a direct British colony in 1841 and "
            u"received a large measure of self-government following the New "
            u"Zealand Constitution Act 1852. New Zealand chose not to take "
            u"part in Australian Federation and assumed complete "
            u"self-government as the Dominion of New Zealand on 26 September "
            u"1907, Dominion Day, by proclamation of King Edward VII.")

    @inlineCallbacks
    def test_get_content_shorter(self):
        yield self.assert_api_result(
            self.wikipedia.get_content(
                'Dominion of New Zealand', 0, length_limit=200),
            u"The '''Dominion of New Zealand''' is the former name of the "
            u"[[Realm of New Zealand]].\n\n[[Image:Dominion Day New "
            u"Zealand.jpg|thumb|250px|right|[[William Plunket, 5th Baron "
            u"Plunket|Lord Plunket]] declar")

    @inlineCallbacks
    def test_get_content_infobox(self):
        yield self.assert_api_result(
            self.wikipedia.get_content('Hellinsia tripunctatus', 0),
            u"{{Taxobox\n| name = \n| image = \t\n| image_width = 250px\n| "
            u"image_caption = \n| regnum = [[Animal]]ia\n| phylum = "
            u"[[Arthropod]]a\n| classis = [[Insect]]a\n| ordo = [[Lepidoptera"
            u"]]\n| familia = [[Pterophoridae]]\n| subfamilia = \n| tribus = "
            u"\n| genus = ''[[Hellinsia]]''\n| species = '''''H. tripunctatus"
            u"'''''\n| binomial = ''Hellinsia tripunctatus''\n| "
            u"binomial_authority = (Walsingham, 1881)<ref>["
            u"http://www.afromoths.net/species/show/39458 Afro Moths]</ref>\n"
            u"| synonyms = \n*''Aciptilus tripunctatus'' <small>Walsin")

    @inlineCallbacks
    def test_get_content_small(self):
        yield self.assert_api_result(
            self.wikipedia.get_content('Hellinsia tripunctatus', 1),
            u'==References==\n{{Reflist}}\n\n[[Category:Hellinsia|tripunctatus'
            u']]\n[[Category:Butterflies and moths of Africa]]\n[[Category:'
            u'Insects of South Africa]]\n\n{{Oidaematophorini-stub}}\n\n'
            u'[[vi:Hellinsia tripunctatus]]')

    @inlineCallbacks
    def test_unicode_normalization(self):
        yield self.assert_api_result(
            self.wikipedia.get_content('Kenya', 4, content_type='text'),
            u"Prehistory:\nGiant crocodile fossils have been discovered in "
            u"Kenya, dating from the Mesozoic Era, over 200 million "
            u"years ago. The fossils were found in an excavation conducted "
            u"by a team from the University of Utah and the National Museums "
            u"of Kenya in July-August 2004 at Lokitaung Gorge, near Lake "
            u"Turkana.\nFossils found in East Africa suggest that primates "
            u"roamed the area more than 20 million years ago. Recent "
            u"finds near Kenya's Lake Turkana indicate that hominids such as "
            u"Homo habilis (1.8 and 2.5")


class WikipediaWorkerTestCase(TestCase, FakeHTTPTestCaseMixin):
    transport_name = 'sphex'

    timeout = 10

    @inlineCallbacks
    def setUp(self):
        yield self.start_webserver(WIKIPEDIA_RESPONSES)
        self.broker = FakeAMQPBroker()
        self._workers = []
        yield self.get_worker()

    @inlineCallbacks
    def tearDown(self):
        for w in self._workers:
            yield w.stopWorker()
        yield self.stop_webserver()

    @inlineCallbacks
    def get_worker(self, **config_extras):
        if hasattr(self, 'worker'):
            self._workers.remove(self.worker)
            yield self.worker.stopWorker()
        config = {
            'transport_name': self.transport_name,
            'worker_name': 'wikitest',
            'sms_transport': 'sphex',
            'api_url': self.url,
            }
        config.update(config_extras)
        self.worker = get_stubbed_worker(WikipediaWorker, config, self.broker)
        self._workers.append(self.worker)
        yield self.worker.startWorker()
        self.wikipedia = self.worker.wikipedia
        returnValue(self.worker)

    def mkmsg_in(self, content):
        return TransportUserMessage(
            from_addr='+41791234567',
            to_addr='9292',
            message_id='abc',
            transport_name=self.transport_name,
            transport_type='ussd',
            transport_metadata={},
            content=content,
            )

    def rkey(self, name):
        return "%s.%s" % (self.transport_name, name)

    def dispatch(self, message, rkey=None, exchange='vumi'):
        if rkey is None:
            rkey = self.rkey('inbound')
        self.broker.publish_message(exchange, rkey, message)
        return self.broker.kick_delivery()

    def get_dispatched_messages(self):
        return self.broker.get_messages('vumi', self.rkey('outbound'))

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
    def test_happy_flow_wikitext(self):
        yield self.dispatch(self.mkmsg_in(None))
        self.assertEqual('What would you like to search Wikipedia for?',
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('africa'))
        self.assertEqual('\n'.join([
                    u'1. Africa',
                    u'2. .africa',
                    u'3. African American',
                    u'4. North Africa',
                    u'5. Kenya',
                    u'6. Sub-Saharan Africa',
                    u'7. Africa (Roman province)',
                    u'8. African people',
                    ]),
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('1'))
        self.assertEqual('\n'.join([
                    u'1. Africa',
                    u'2. Etymology',
                    u'3. History',
                    u'4. Geography',
                    u'5. Biodiversity',
                    u'6. Politics',
                    u'7. Economy',
                    u'8. Demographics',
                    u'9. Languages',
                    u'10. Culture',
                    u'11. Religion',
                    ]),
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('2'))
        content = (
            u'==Etymology==\n\n[[Afri]] was a Latin name used to refer to the '
            u'[[Carthaginians]] who dwelt in [[North Africa]] in modern-day '
            u'[[Tunisia]]. Their name is usually connected with [[Phoenician '
            u'language|Phoenician]] \'\'afar\'\', "dust", but a 1981 '
            u'hypothesis<ref>[http://michel-desfayes.org/namesofcountries.html'
            u' Names of countries], Decret and Fantar, 1981</ref> has '
            u'asserted that it stems from the [[Berber language|Berber]] word '
            u'\'\'ifri\'\' or \'\'ifran\'\' meaning "cave" and "caves", in '
            u'reference to cave dweller')
        self.assertEqual(
            "%s...\n(Full content sent by SMS.)" % (content[:100],),
            self.get_dispatched_messages()[-2]['content'])
        self.assertEqual(content[:250],
                         self.get_dispatched_messages()[-1]['content'])

    @inlineCallbacks
    def test_happy_flow_unicode(self):
        yield self.dispatch(self.mkmsg_in(None))
        self.assertEqual('What would you like to search Wikipedia for?',
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('zundapp'))
        self.assertEqual('\n'.join([
                    u'1. Z\xfcndapp',
                    u'2. Z\xfcndapp Janus',
                    u'3. List of Cars characters',
                    u'4. Casal',
                    u'5. Berliner Motor Corporation',
                    u'6. Lightning McQueen',
                    u'7. Thomas Kretschmann',
                    u'8. BMW R75',
                    ]),
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('1'))
        self.assertEqual('\n'.join([
                    u'1. Z\xfcndapp',
                    u'2. See also',
                    u'3. References',
                    u'4. External links',
                    ]),
                         self.get_dispatched_messages()[-1]['content'])

        yield self.dispatch(self.mkmsg_in('2'))
        content = (
            u"==See also==\n[[Image:Z\u00fcndappN\u00e4hmaschine2.jpg|thumb"
            u"|A Z\u00fcndapp sewing machine]]\n*[[BMW motorcycles|BMW "
            u"(motorcycles)]]\n*[[\u010cezeta]]\n*[[Heinkel]]\n*[[Maico]]\n"
            u"*[[MZ Motorrad- und Zweiradwerk GmbH]]")
        self.assertEqual(
            "%s...\n(Full content sent by SMS.)" % (content[:100],),
            self.get_dispatched_messages()[-2]['content'])
        self.assertEqual(content[:250],
                         self.get_dispatched_messages()[-1]['content'])

    @inlineCallbacks
    def test_happy_flow_text(self):
        self.worker.content_type = 'text'
        yield self.search_for_content('africa', 1, 2)

        content = (
            u'Etymology:\nAfri was a Latin name used to refer to the '
            u'Carthaginians who dwelt in North Africa in modern-day Tunisia. '
            u'Their name is usually connected with Phoenician afar, "dust", '
            u'but a 1981 hypothesis has asserted that it stems from the '
            u'Berber word ifri or ifran meaning "cave" and "caves", in '
            u'reference to cave dwellers. Africa or Ifri or Afer is the '
            u'name of Banu Ifran from Algeria and Tripolitania (Berber Tribe '
            u'of Yafran).\nUnder Roman rule, Carthage became the capital '
            u'of Africa Provin')
        self.assertEqual(
            "%s...\n(Full content sent by SMS.)" % (content[:100],),
            self.get_dispatched_messages()[-2]['content'])
        self.assertEqual(content[:250],
                         self.get_dispatched_messages()[-1]['content'])
