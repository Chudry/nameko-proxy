from logging import getLogger

from kombu import Connection
from kombu.messaging import Consumer
from kombu.mixins import ConsumerMixin
from nameko.amqp import verify_amqp_uri
from nameko.constants import (
    AMQP_URI_CONFIG_KEY, DEFAULT_SERIALIZER, SERIALIZER_CONFIG_KEY)

logger = getLogger()


class QueueConsumer(ConsumerMixin):

    PREFETCH_COUNT_CONFIG_KEY = 'PREFETCH_COUNT'
    DEFAULT_KOMBU_PREFETCH_COUNT = 10

    def __init__(self, spawn_cls, timeout=None):
        self.spawn_cls = spawn_cls
        self.timeout = timeout
        self.replies = {}
        self._managed_threads = []

        self.provider = None
        self.queue = None
        self.prefetch_count = None
        self.serializer = None
        self.accept = []
        self._connection = None

    @property
    def connection(self):
        if not self._connection:
            self._connection = Connection(self.provider.container.config[AMQP_URI_CONFIG_KEY])
        return self._connection

    def register_provider(self, provider):
        logger.debug("QueueConsumer registering...")
        self.provider = provider
        self.queue = provider.queue
        self.serializer = provider.container.config.get(SERIALIZER_CONFIG_KEY, DEFAULT_SERIALIZER)
        self.prefetch_count = self.provider.container.config.get(
            self.PREFETCH_COUNT_CONFIG_KEY, self.DEFAULT_KOMBU_PREFETCH_COUNT)
        self.accept = [self.serializer]

        verify_amqp_uri(provider.container.config[AMQP_URI_CONFIG_KEY])

        self.start()

    def start(self):
        logger.info("QueueConsumer starting...")
        gt = self.spawn_cls(self.run)
        self._managed_threads.append(gt)
        gt.link(self._handle_thread_exited)

    def _handle_thread_exited(self, gt):
        self._managed_threads.remove(gt)
        try:
            gt.wait()
        except Exception as error:
            logger.error("Managed thread end with error: %s", error)

    def on_message(self, body, message):
        correlation_id = message.properties.get('correlation_id')
        if correlation_id not in self.provider._reply_events:
            logger.debug(
                "Unknown correlation id: %s", correlation_id)

        self.replies[correlation_id] = (body, message)

    def unregister_provider(self, _):
        self.connection.close()
        self.should_stop = True

    def get_consumers(self, _, channel):
        consumer = Consumer(channel, queues=[self.provider.queue], accept=self.accept,
                            no_ack=False, callbacks=[self.on_message, self.provider.handle_message])
        consumer.qos(prefetch_count=self.prefetch_count)
        return [consumer]

    @staticmethod
    def ack_message(msg):
        msg.ack()
