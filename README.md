# SynapSteward Climate Control Module

## Overview

This is a module for the SynapSteward system that evaluates environmental
sensor data and forwards out-of-bounds readings to a local LLM for suggested
actions. The module connects to a NATS server to consume sensor data and
configuration messages. It evaluates the sensor data against pre-defined
boundaries and records historical sensor data for evaluation. When data is
out-of-bounds, it forwards the data history for further evaluation.

## Features

- Connects to a NATS server to consume sensor data and configuration messages.
- Evaluates sensor data against pre-defined boundaries.
- Records historical sensor data for evaluation.
- Forwards out-of-bounds data history for further evaluation and suggestions.
- Integrates with an actuator system to evaluate alerts and suggest required
  actions using a local LLM (Large Language Model).

## Requirements

- Python 3.6+
- asyncio
- argparse
- nats-py
- pydantic
- uv (recommended)

## Installation

1. Ensure Python 3.6 or higher is installed on your system.
2. Install the required dependencies by running:
   ```bash
   pip install nats-py pydantic
   ```

## ClimateCore Usage

Run the script using the following command:

```bash
uv run climatecore.py [options]
```

### Options

- `--nats-server`: Specify the NATS server URL. Default is `nats://localhost:4222`.
- `--nats-sensor-stream`: Specify the NATS input sensor stream name. Default is `environmental_sensors`.
- `--nats-alerts-subject-prefix`: Specify the NATS alerts subject prefix. Default is `alerts.climatecore`.
- `--nats-config-subject`: Specify the NATS configuration message subject. Default is `config.climatecore`.
- `--debug`: Enable debug logging for more verbose output.

### Configuration

Sensor bounds configuration is updated dynamically via messages received on the
specified NATS configuration subject.

## Actuator Usage

The `actuator.py` script is responsible for picking up alerts from the NATS
server, using a local LLM model to evaluate actions, and then publishing
suggested actions back to the NATS server.

Run the script using:

```bash
uv run actuator.py [options]
```

#### Options

- `--nats-server`: Specify the NATS server URL. Default is `nats://localhost:4222`.
- `--nats-alerts-stream`: Specify the NATS alerts stream name. Default is `alerts_climatecore`.
- `--nats-memory-subject`: Specify the NATS memory message subject. Default is `memory.climatecore`.
- `--nats-memory-stream`: Specify the NATS memory stream name. Default is `memory_climatecore`.
- `--nats-actions-subject`: Specify the NATS actions message subject. Default is `notifications.climatecore`.
- `--nats-upstream-subject-prefix`: Specify the NATS subject prefix to pass messages to higher-level modules. Default is `upstream.climatecore`.
- `--llm-model`: LLM model to use for analysis. Default is from the environment variable `LLM_MODEL` or falls back to `"mlx-community/Mistral-Small-24B-Instruct-2501-4bit"`.
- `--debug`: Enable debug logging for more verbose output.
