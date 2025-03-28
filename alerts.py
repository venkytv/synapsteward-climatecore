#!/usr/bin/env python3

# Script to pick alerts from a NATS server, use an LLM model to evaluate them,
# and compute an alert colour based on the evaluation. The alert colour is then
# published back to the NATS server.

import argparse
import asyncio
from datetime import datetime
import llm
import logging
import nats
import os
import pydantic
import re
from retry import retry
import time
from typing_extensions import Annotated

from models import Alert
from stream import Stream

NATS_STREAM_TIMEOUT = 3

def is_valid_hex_colour(colour: str) -> str:
    if re.match(r"^#[0-9a-fA-F]{6}$", colour):
        return colour
    raise ValueError(f"Invalid hex colour: {colour}")

class Colour(pydantic.BaseModel):
    colour: Annotated[str, pydantic.AfterValidator(is_valid_hex_colour)]
    brightness: pydantic.conint(ge=1, le=100) = 50
    reason: str
    state: str = ""

sample_colour = Colour(
    colour="#FF0000",
    brightness=50,
    reason="reason for the colour",
    state="rule:green based colors for CO2, blue for humidity, cooler shades for living room, warmer for bedroom;co2_living_room=#2ada75;co2_bedroom=#87da2a",
)

def current_state_prompt(alerts: list[Alert]) -> str:
    prompt = f"""
    You are an expert in climate monitoring and sensor data. The time now is
    {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}. Below are climate sensor
    alerts from a monitoring system. Your task is to analyse these alerts and
    determine the current state of each sensor. The alerts are in time order.
    The last event for each sensor is the current state. The alerts are:

    - {"\n    - ".join([alert.model_dump_json() for alert in alerts])}

    Print a concise summary of the current state of the sensors.
"""
    return prompt

def construct_prompt(summary: str, state: str) -> str:
    prompt = f"""
    You are an expert in climate monitoring and sensor data
    analysis. The time now is {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.

    Below is the summary of the climate sensor alerts from a monitoring system.
    Your task is to suggest a colour for the current state of the system in
    hexadecimal format, such as #FF0000 for red. A sample colour is shown below:

    {sample_colour.model_dump_json()}

    If there is no need for an alert, you can suggest use #000000 for black.

    Note that the "state" value here is for demonstration only. You can use it
    to store state in any format or encoding you like as long as it can be
    stored in a JSON object. As you will be computing the colours for the alerts
    depending on the specific sensor value, urgency, location, etc., you can use
    the "state" field to keep track of the mapping of colours to specific sensors,
    or any other state you need to maintain. This field will be passed back to
    you in the next run.

    The objective is to use the state field to make sure the colours are
    consistent across runs and have some logic to how they are assigned for
    different sensors, locations, etc. Attempt to map the colours logically to
    the sensor names and locations. For instance, you could use green for CO2,
    blue for humidity, and red for temperature, with cooler shades for the
    bedroom and warmer shades for the living room. Aim to make the colours
    easily distinguishable and unambiguous. You can choose from the full range
    of colours supported by the Philips Hue system.

    Do store the logic for the colour assignment in the "state" field so that
    you can refer to it in the next run. The "state" field can also be used to
    keep track of anything else you need to maintain across runs.  Note that
    the sensors can be one of temperature, humidity, or CO2.

    Use the "brightness" field to indicate the urgency of the alert, with 100
    being the most urgent and 1 being the least. Do not set the brightness to
    100 unless the alert is very urgent. Note that the alerts will be addressed
    by humans, and so might not be addressed immediately. Set the urgency
    appropriately based on the real world implications of the alert. For
    instance, a high CO2 of 1200 ppm is something that can be addressed within
    a few hours, while a high CO2 of 3000 ppm is something that needs to be
    addressed immediately. Similarly, high humidity of 70% is not very urgent.

    If there are multiple alerts, pick the most urgent one for the colour and
    brightness.

    The last state you provided was: "{state}"

    The current sensor alerts summary is:

    {summary}

    Do also consider the time of the day, the day of the week, and any other
    information you think is relevant to the colour assignment.

    IMPORTANT: RETURN JUST ONE JSON OBJECT WITH THE COLOUR, REASON, AND STATE,
               OR AN OBJECT WITH #000000 COLOUR IF YOU HAVE NO RECOMMENDATIONS.
               DO NOT ADD ANY ADDITIONAL COMMENTS.
"""

    return prompt

def save_state(state_file: str, state: str):
    with open(state_file, "w") as f:
        f.write(state)

def load_state(state_file: str) -> str:
    try:
        with open(state_file, "r") as f:
            return f.read()
    except FileNotFoundError:
        return ""

@retry(pydantic.ValidationError, tries=3, delay=2)
def load_alert_colour(llm: llm.models.Model, prompt: str) -> Colour:
    colour = llm.prompt(prompt)
    logging.debug(f"LLM response: {colour}")

    # Strip out everything from the response outside the outer JSON braces
    colour = re.sub(r".*?({.*}).*", r"\1", colour.text(), flags=re.DOTALL)
    logging.debug("Cleaned action: %s", colour)

    # Load the response into a Colour object
    return Colour.model_validate_json(colour)

async def main():
    default_llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    default_nats_server = os.getenv("NATS_SERVER", "nats://localhost:4222")

    parser = argparse.ArgumentParser(
        description="ClimateCore Alerts",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--nats-server", type=str, help="NATS server URL",
                        default=default_nats_server)
    parser.add_argument("--nats-alerts-stream", type=str,
                        help="NATS alerts stream name",
                        default="alerts_climatecore")
    parser.add_argument("--nats-consumer", type=str,
                        help="NATS consumer name",
                        default="alert_colours_consumer")
    parser.add_argument("--nats-alert-colours-subject", type=str,
                        help="NATS alert colours message subject",
                        default="alert_colours.climatecore")
    parser.add_argument("--state-file", type=str,
                        help="File to store state in",
                        default=os.path.expanduser("~/.climatecore.alerts.state"))
    parser.add_argument("--reload-interval", type=int,
                        help="Interval to reload the alerts in seconds",
                        default=900)
    parser.add_argument("--llm-model", default=default_llm_model,
                        help="LLM model to use for analysis")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    loglevel = logging.INFO
    if args.debug:
        loglevel = logging.DEBUG
    logging.basicConfig(level=loglevel,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    nc = await Stream.connect(args.nats_server)

    # Load LLM model
    logging.debug(f"Loading LLM model {args.llm_model}")
    llm_model = llm.get_model(args.llm_model)

    # Load state
    state = load_state(args.state_file)

    while True:
        # Generate a random consumer name, alphanumerics and underscores only
        consumer = f"{args.nats_consumer}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        logging.info(f"Starting a fresh run with consumer: {consumer}")

        all_alerts = []

        try:
            alerts = Stream(
                connection=nc,
                stream=args.nats_alerts_stream,
                consumer=consumer,
                timeout=NATS_STREAM_TIMEOUT,
                model=Alert,
            )

            timeout = time.time() + args.reload_interval
            while time.time() < timeout:
                msgs = await alerts.get_messages(nmsgs=50)
                logging.debug(f"{len(msgs)} alerts received")

                if msgs:
                    all_alerts.extend(msgs)
                    prompt = current_state_prompt(all_alerts)
                    summary = llm_model.prompt(prompt)
                    logging.info(f"Current state summary: {summary}")

                    prompt = construct_prompt(summary, state)
                    colour = load_alert_colour(llm_model, prompt)
                    logging.info(f"Colour: {colour}")

                    # Save the state
                    state = colour.state
                    save_state(args.state_file, state)

                    # Publish the colour
                    await nc.publish(args.nats_alert_colours_subject,
                                     colour.model_dump_json().encode("utf-8"))

        finally:
            logging.debug(f"Deleting consumer {consumer}")
            await alerts.jetstream.delete_consumer(args.nats_alerts_stream, consumer)

if __name__ == "__main__":
    asyncio.run(main())
