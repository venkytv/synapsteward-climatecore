# Helper module to read data from NATS streams

import asyncio
import logging
import nats
import pydantic
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

class Stream:
    EPHEMERAL = "___ephemeral___"

    @classmethod
    async def connect(cls, url: str, abort_on_error: bool = True) -> nats.NATS:
        async def error_cb(e):
            logger.error("Error: %s", e)
            if abort_on_error:
                sys.exit(1)
            raise e
        logger.debug("Connecting to NATS server at %s", url)
        connection = await nats.connect(url, error_cb=error_cb)
        return connection

    def __init__(self,
                 connection: nats.NATS,
                 stream: Optional[str] = None,
                 subject: Optional[str] = None,
                 consumer: Optional[str] = None,
                 model: Any = None,
                 timeout: int = 1):
        self.connection = connection
        self.jetstream = self.connection.jetstream()
        self.stream = stream
        self.subject = subject
        self.model = model
        self.timeout = timeout

        if consumer == self.EPHEMERAL:
            self.consumer = None
        elif not consumer:
            self.consumer = f"{stream}_consumer"
        else:
            self.consumer = consumer

    async def publish(self, data: Any) -> None:
        if not self.subject:
            raise ValueError("Subject is required to publish messages")
        if self.model:
            raw_data = self.model.model_dump_json(data)
        else:
            raw_data = str(data)

        logger.debug("Publishing message to subject %s: %s", self.subject, raw_data)
        await self.connection.publish(self.subject, raw_data.encode())

    async def get_messages(self, nmsgs: int = 1) -> list[Any]:
        if not self.stream:
            raise ValueError("Stream is required to fetch messages")

        logger.debug("Fetching %d messages from stream %s", nmsgs, self.stream)
        psub = await self.jetstream.pull_subscribe("", stream=self.stream,
                                                     durable=self.consumer)
        response: list[Any] = []

        try:
            logger.debug("Fetching messages with timeout %d", self.timeout)
            msgs = await psub.fetch(batch=nmsgs, timeout=self.timeout)
            if not msgs:
                logger.debug("No messages found")
                return response

            acks = []
            for msg in msgs:
                raw_data = msg.data.decode()
                if self.model:
                    try:
                        data = self.model.model_validate_json(raw_data)
                    except pydantic.ValidationError as e:
                        logger.error("Error validating data: %s: %s", e, raw_data)
                        continue
                    response.append(data)
                else:
                    response.append(raw_data)

                acks.append(asyncio.create_task(msg.ack()))

            # Wait for all acks to complete
            logger.debug("Acknowledging messages")
            await asyncio.gather(*acks)

        except nats.errors.TimeoutError:
            logger.debug("Timeout fetching messages")

        return response
