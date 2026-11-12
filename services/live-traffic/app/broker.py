"""A very small publish/subscribe broker (T089).

Publish/subscribe in one sentence: the code that PRODUCES data does not call
the code that USES the data directly. Instead it "publishes" a message on a
named topic, and anything that "subscribed" to that topic receives it.

    publisher  --publish("traffic.locations", message)-->  broker
    broker     --calls every subscriber function-------->  subscribers

This broker lives inside the service's own memory. It is enough for one
service. A real system would use a separate broker product so that messages
can travel between different services and machines.
"""

import logging
import threading


logger = logging.getLogger("live_traffic_broker")


class Broker:
    def __init__(self):
        # topic name -> list of functions to call when a message arrives
        self.subscribers = {}
        self.published_count = 0
        self.lock = threading.Lock()

    def subscribe(self, topic, callback):
        """Ask to be called every time a message is published on this topic."""
        with self.lock:
            if topic not in self.subscribers:
                self.subscribers[topic] = []
            self.subscribers[topic].append(callback)

    def publish(self, topic, message):
        """Send a message to every subscriber of this topic."""
        with self.lock:
            self.published_count = self.published_count + 1
            callbacks = list(self.subscribers.get(topic, []))

        for callback in callbacks:
            try:
                callback(message)
            except Exception:
                # One broken subscriber must not stop the others or the publisher.
                logger.exception("A subscriber failed on topic %s", topic)
