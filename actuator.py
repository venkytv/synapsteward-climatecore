#!/usr/bin/env python3

# Script to pick alerts from a NATS server, use a local LLM model to evaluate
# the actions, and publish the suggested actions back to the NATS server.

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
import sys

from models import Alert, Memory, Notification
from stream import Stream

class Action(pydantic.BaseModel):
    action: str
    reason: str

sample_action = Action(action="action details", reason="reason for the action")
sample_no_action = Action(action="", reason="reason for no action")

def construct_prompt(alerts: list[Alert], memory: list[Memory]) -> str:
    prompt = f"""
    You are an expert in climate monitoring and sensor data
    analysis. The time now is {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.

    Below is a set of climate sensor alerts from a monitoring system. Your task
    is to analyse these alerts and suggest ZERO OR ONE recommendations for
    action, such as adjusting ventilation, investigating anomalies, or
    setting new sensor bounds.
    The recommendation, if any, should be in the following format in JSON:
    {sample_action.model_dump_json()}
    If you have no recommendations, you can suggest an a reponse in the
    following format in JSON:
    {sample_no_action.model_dump_json()}
    Err on the side of not suggesting an action if you are unsure.

    - {"\n    - ".join([alert.model_dump_json() for alert in alerts])}

    Also take into account the following list of recent actions suggested by
    you. If you have already suggested an action for a similar alert, you
    should not suggest the same action again, unless the context has changed
    or significant time has passed since then. Err on the side of not
    overwhelming the user with too many actions.

    - {"\n    - ".join([mem.model_dump_json() for mem in memory])}

    Take into account the time of the day (not pleasant to wake someone up at
    3am for a minor issue), the day of the week, and the urgency of the alert.

    IMPORTANT: RETURN JUST ONE JSON OBJECT WITH THE ACTION AND REASON, OR AN
               EMPTY LIST IF YOU HAVE NO RECOMMENDATIONS. DO NOT ADD ANY
               ADDITIONAL COMMENTS.
"""

    return prompt

@retry(pydantic.ValidationError, tries=3, delay=2)
def load_action(llm: llm.models.Model, prompt: str) -> Action:
    action = llm.prompt(prompt)
    logging.debug("LLM suggested action: %s", action)

    action = str(action)

    if not action or re.match(r"\[\s*\]", action):
        # Model suggested no action
        return Action(action="", reason="No action suggested")

    # Strip out everything from the response outside the outer JSON braces
    action = re.sub(r".*?({.*}).*", r"\1", action, flags=re.DOTALL)
    logging.debug("Cleaned action: %s", action)

    # Load the response into the pydantic model
    return Action.model_validate_json(action)

async def main():
    default_llm_model = os.environ.get("LLM_MODEL",
                                   "mlx-community/Mistral-Small-24B-Instruct-2501-4bit")
    default_nats = os.environ.get("NATS", "nats://localhost:4222")

    parser = argparse.ArgumentParser(
        description="ClimateCore Actuator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--nats-server", type=str, help="NATS server URL",
                        default=default_nats)
    parser.add_argument("--nats-alerts-stream", type=str,
                        help="NATS alerts stream name",
                        default="alerts_climatecore")
    parser.add_argument("--nats-memory-subject", type=str,
                        help="NATS memory message subject",
                        default="memory.climatecore")
    parser.add_argument("--nats-memory-stream", type=str,
                        help="NATS memory stream name",
                        default="memory_climatecore")
    parser.add_argument("--nats-actions-subject", type=str,
                        help="NATS actions message subject",
                        default="notifications.climatecore")
    parser.add_argument("--nats-upstream-subject", type=str,
                        help="NATS subject to pass on messages to higher-level modules",
                        default="upstream.climatecore")
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

    logging.debug("Connecting to NATS server at %s", args.nats_server)
    nc = await nats.connect(args.nats_server)

    alerts = Stream(
        connection=nc,
        stream=args.nats_alerts_stream,
        model=Alert,
    )
    msgs = await alerts.get_messages(nmsgs=50)
    if not msgs:
        logging.debug("No messages found")
        sys.exit(0)

    logging.debug("Messages: %s", msgs)

    memory = Stream(
        connection=nc,
        stream=args.nats_memory_stream,
        subject=args.nats_memory_subject,
        consumer=Stream.EPHEMERAL,
        model=Memory,
    )
    memory_msgs_task = asyncio.create_task(memory.get_messages(nmsgs=50))

    # Load LLM model
    logging.debug("Loading model %s", args.llm_model)
    llm_model = llm.get_model(args.llm_model)

    memory_msgs = await memory_msgs_task
    logging.debug("Memory messages: %s", memory_msgs)

    prompt = construct_prompt(msgs, memory_msgs)
    logging.debug("Prompt: %s", prompt)

    action = load_action(llm_model, prompt)
    logging.debug("Action: %s", action)

    if action.action:
        logging.debug("Publishing alert and saving to memory: %s", action)
        alert = Stream(
            connection=nc,
            subject=args.nats_actions_subject,
            model=Notification,
        )
        upstream = Stream(
            connection=nc,
            subject=args.nats_upstream_subject,
            model=Action,
        )
        resp = await asyncio.gather(
            alert.publish(
                Notification(title=action.action, message=action.reason)),
            memory.publish(
                Memory(message=f"{action.action}: {action.reason}")),
            upstream.publish(action),
        )
        logging.debug("Response: %s", resp)
    else:
        logging.info("No action suggested; reason: %s", action.reason)

if __name__ == "__main__":
    asyncio.run(main())
