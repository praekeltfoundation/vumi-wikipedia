"""Tests for vumi.demos.wikipedia."""

from twisted.trial.unittest import TestCase
from twisted.internet.defer import inlineCallbacks, returnValue

from vumi.tests.fake_amqp import FakeAMQPBroker
from vumi.tests.utils import get_stubbed_worker
from vumi.message import TransportUserMessage
from vumi.demos.wikipedia import WikipediaWorker


class WikipediaWorkerTestCase(TestCase):
    transport_name = 'sphex'

    timeout = 10

    @inlineCallbacks
    def setUp(self):
        self.broker = FakeAMQPBroker()
        self._workers = []
        self.worker = yield self.get_worker()

    @inlineCallbacks
    def tearDown(self):
        for w in self._workers:
            yield w.stopWorker()

    @inlineCallbacks
    def get_worker(self, config=None):
        if not config:
            config = {
                'worker_name': 'wikitest',
                'sms_transport': 'sphex',
                }
        config.setdefault('transport_name', self.transport_name)
        worker = get_stubbed_worker(WikipediaWorker, config, self.broker)
        self._workers.append(worker)
        yield worker.startWorker()
        returnValue(worker)

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
    def test_happy_flow(self):
        # TODO: Stub out the API calls.
        yield self.dispatch(self.mkmsg_in(None))
        self.assertEqual('What would you like to search Wikipedia for?',
                         self.get_dispatched_messages()[-1]['content'])
        yield self.dispatch(self.mkmsg_in('africa'))
        self.assertEqual('\n'.join([
                    '1. Africa',
                    '2. .africa',
                    '3. African American',
                    '4. North Africa',
                    '5. Kenya',
                    '6. Sub-Saharan Africa',
                    '7. Africa (Roman province)',
                    '8. African people',
                    '9. Confederation of African Football']),
                         self.get_dispatched_messages()[-1]['content'])
        yield self.dispatch(self.mkmsg_in('1'))
        self.assertEqual('\n'.join([
                    '1. Africa',
                    '2. Etymology',
                    '3. History',
                    '4. Geography',
                    '5. Biodiversity',
                    '6. Politics',
                    '7. Economy',
                    '8. Demographics',
                    '9. Languages',
                    '10. Culture',
                    '11. Religion',
                    '12. Territories and regions',
                    '13. See also',
                    '14. References',
                    '15. Further reading',
                    '16. External links',
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
