
from kafka import KafkaProducer
from omegaconf import OmegaConf
import logging

from .types import KafkaEvent
from .event_processor import EventListener

LOGGER = logging.getLogger("dna.node.event")


class KafkaEventPublisher(EventListener):
    def __init__(self, conf:OmegaConf) -> None:
        try:
            self.producer = KafkaProducer(bootstrap_servers=conf.bootstrap_servers)
            self.topic = conf.topic
            LOGGER.info(f"connect kafka-servers: {conf.bootstrap_servers}")
        except BaseException as e:
            LOGGER.error(f"fails to connect KafkaBrokers: {conf.bootstrap_servers}")
            raise e

    def close(self) -> None:
        super().close()
        self.producer.close(1)

    def handle_event(self, ev:KafkaEvent) -> None:
        if isinstance(ev, KafkaEvent):
            key = ev.key()
            value = ev.serialize()
            self.producer.send(self.topic, value=value, key=key.encode('utf-8'))
            # print(value)

    def flush(self) -> None:
        self.producer.flush()