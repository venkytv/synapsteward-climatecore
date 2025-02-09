# SynapSteward Climate Control Module

## Overview

This is a module for the SynapSteward system that evaluates environmental
sensor data and forwards out-of-bounds readings to OpenAI for suggested
actions. The module connects to a NATS server to consume sensor data and
configuration messages. It evaluates the sensor data against pre-defined
boundaries and records historical sensor data for evaluation. When data is
out-of-bounds, it forwards the data history for further evaluation.

## Features

- Connects to a NATS server to consume sensor data and configuration messages.
- Evaluates sensor data against pre-defined boundaries.
- Records historical sensor data for evaluation.
- When data is out-of-bounds, it forwards the data history for further evaluation.

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

## Usage

Run the script using the following command:

```bash
uv run climatecore.py [options]
```

### Options

- `--nats-server`: Specify the NATS server URL. Default is `nats://localhost:4222`.
- `--nats-stream`: Specify the NATS stream name. Default is `environmental_sensors`.
- `--nats-config-subject`: Specify the NATS configuration message subject. Default is `config.climatecore`.
- `--history-length`: Number of historical values to store for evaluation. Default is 50.
- `--debug`: Enable debug logging for more verbose output.

## Configuration

Sensor bounds configuration is updated dynamically via messages received on the
specified NATS configuration subject.
