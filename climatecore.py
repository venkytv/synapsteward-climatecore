#!/usr/bin/env python3

# Script to read environmental data from sensors published to a NATS server.
# The data is evaluated, and if out of bounds, the events are pass on to
# OpenAI for evaluation, and the suggested actions are published to the NATS
# server.

import argparse
import asyncio
from collections.abc import Awaitable
from datetime import datetime
import json
import logging
import nats
import pydantic

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class SensorData(pydantic.BaseModel):
    name: str
    device_id: str
    location: str
    value: float
    timestamp: datetime

class SensorBounds(pydantic.BaseModel):
    sensor: str
    min: float
    max: float

class Alert(pydantic.BaseModel):
    message: str = "Sensor data out of bounds"
    sensor_data: SensorData
    sensor_bounds: SensorBounds

class Config(pydantic.BaseModel):
    nats_server: str
    nats_sensor_stream: str
    nats_alerts_subject_prefix: str
    nats_config_subject: str

    sensor_bounds: dict[str, SensorBounds] = {}

async def config_listener(js: nats.js.JetStreamContext, config: Config):
    """Listen for configuration messages and update the configuration"""

    # Create a subscription to the config stream
    config_consumer_config = nats.js.api.ConsumerConfig(
        deliver_policy=nats.js.api.DeliverPolicy.LAST
    )
    sub = await js.pull_subscribe(config.nats_config_subject,
                                  config=config_consumer_config)

    while True:
        try:
            msgs = await sub.fetch(batch=1, timeout=2)

            for msg in msgs:
                logger.debug("Received config message: %s" % (msg.data.decode()))
                try:
                    data = json.loads(msg.data)
                    for sensor, bounds in data.items():
                        sensor_bounds = SensorBounds(sensor=sensor, **bounds)
                        config.sensor_bounds[sensor] = sensor_bounds
                        logger.info("Updated sensor bounds: %s" % (sensor_bounds))
                except pydantic.ValidationError as e:
                    logger.error("Invalid sensor bounds: %s: %s" % (msg.data.decode(), e))
                    # TODO: Publish an error message back into NATS for some other service to handle

                # Acknowledge the message, even if it was invalid
                await msg.ack()

        except asyncio.TimeoutError:
            pass

async def main(config: Config):
    async def connection_error_cb(e):
        logger.warning("Connection error: %s" % (e))

    nc = await nats.connect(config.nats_server,
                            disconnected_cb=connection_error_cb,
                            error_cb=connection_error_cb)
    js = nc.jetstream() # Create a JetStream context

    # Create a background task to listen for configuration updates
    asyncio.create_task(config_listener(js, config))

    # Wait for configuration to be received
    logger.info("Waiting for configuration...")
    while len(config.sensor_bounds) == 0:
        await asyncio.sleep(0.1)

    logger.debug("Configuration received: %s" % (config.sensor_bounds))

    # Create a consumer for the sensor data stream
    sensor_consumer_config = nats.js.api.ConsumerConfig(
        # Ignore any messages that were sent before the consumer was created
        deliver_policy=nats.js.api.DeliverPolicy.NEW,
    )
    logger.debug("Subscribing to stream: %s" % (config.nats_sensor_stream))
    sub = await js.pull_subscribe("", # Subscribe to all messages
                                  stream=config.nats_sensor_stream,
                                  config=sensor_consumer_config)

    while True:
        try:
            msgs = await sub.fetch(batch=10, timeout=2)

            alert_tasks: list[Awaitable[None]] = []

            for msg in msgs:
                logger.debug("Received message: %s" % (msg))
                data = json.loads(msg.data)
                metadata = msg.metadata
                try:
                    sensor_data = SensorData(timestamp=metadata.timestamp, **data)
                    if sensor_data.name not in config.sensor_bounds:
                        logger.debug("Ignoring sensor data: %s" % (sensor_data))
                        continue

                    logger.debug("Loaded sensor data: %s" % (sensor_data))

                    bounds = config.sensor_bounds[sensor_data.name]
                    if sensor_data.value < bounds.min or sensor_data.value > bounds.max:
                        alert_subject = f"{config.nats_alerts_subject_prefix}.{sensor_data.name}"
                        alert = Alert(sensor_data=sensor_data, sensor_bounds=bounds)
                        logger.warning("Sensor data out of bounds: %s" % (sensor_data))

                        # Publish the alert, which will be picked up by a higher-level module
                        logger.debug("Publishing alert: %s %s" % (alert_subject, alert))
                        task = nc.publish(alert_subject, alert.model_dump_json().encode("utf-8"))
                        alert_tasks.append(task)
                except pydantic.ValidationError as e:
                    logger.error("Invalid sensor data: %s: %s" % (msg.data.decode(), e))
                finally:
                    await msg.ack()

            # Wait for all alerts to be published
            await asyncio.gather(*alert_tasks)

        except asyncio.TimeoutError:
            pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SynapSteward climate control module",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--nats-server", type=str, help="NATS server URL",
                        default="nats://localhost:4222")
    parser.add_argument("--nats-sensor-stream", type=str, help="NATS sensor stream name",
                        default="sensors_environmental")
    parser.add_argument("--nats-alerts-subject-prefix", type=str, help="NATS alerts message subject prefix",
                        default="alerts.climatecore")
    parser.add_argument("--nats-config-subject", type=str, help="NATS config message subject",
                        default="config.climatecore")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    config = Config(
        nats_server=args.nats_server,
        nats_sensor_stream=args.nats_sensor_stream,
        nats_alerts_subject_prefix=args.nats_alerts_subject_prefix,
        nats_config_subject=args.nats_config_subject,
    )

    asyncio.run(main(config))
