"""
Main Service Runner for SolarEdge - Tesla Solar Charging Optimizer
"""

import argparse
import logging
import os
import signal
import sys
import time
import yaml

from solaredge import SolarEdgeClient
from tesla_client import TeslaController
from notifier import WhatsAppNotifier
from controller import SolarChargingController

stop_requested = False


def sig_handler(signum, frame):
    global stop_requested
    logging.info("Received termination signal (%s). Shutting down gracefully...", signum)
    stop_requested = True


def setup_logging(cfg: dict):
    log_cfg = cfg.get("logging", {})
    level_name = log_cfg.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = log_cfg.get("file")

    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers
    )


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        example_path = os.path.join(os.path.dirname(path), "config.example.yaml")
        raise FileNotFoundError(
            f"Configuration file '{path}' not found!\n"
            f"Please copy '{example_path}' to '{path}' and update your settings."
        )

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    global stop_requested
    parser = argparse.ArgumentParser(description="SolarEdge - Tesla Solar Charging Optimizer")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

    setup_logging(config)
    logger = logging.getLogger("main")
    logger.info("Starting SolarEdge - Tesla Solar Charging Optimizer...")

    # Register signals for clean shutdown
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    # Initialize SolarEdge client
    se_cfg = config.get("solaredge", {})
    solaredge_client = SolarEdgeClient(
        host=se_cfg.get("host", "192.168.40.169"),
        port=se_cfg.get("port", 1502),
        unit_id=se_cfg.get("unit_id", 1),
        timeout=se_cfg.get("timeout", 5)
    )

    # Initialize Tesla controller
    tesla_cfg = config.get("tesla", {})
    tesla_client = TeslaController(
        email=tesla_cfg.get("email", ""),
        vin=tesla_cfg.get("vin")
    )
    if not tesla_client.initialize():
        logger.error("Failed to initialize Tesla client. Check authorization or run authenticate_tesla.py.")
        sys.exit(1)

    # Initialize Notifier
    notifier = WhatsAppNotifier(config.get("whatsapp", {}))

    # Initialize Charging Controller
    charging_cfg = config.get("charging", {})
    controller = SolarChargingController(
        solaredge=solaredge_client,
        tesla=tesla_client,
        notifier=notifier,
        config=charging_cfg
    )

    poll_interval = charging_cfg.get("poll_interval_seconds", 30)
    logger.info("Optimizer initialized successfully. Polling loop active (every %ds).", poll_interval)

    # Main Control Loop
    while not stop_requested:
        try:
            controller.evaluate_step()
        except Exception as e:
            logger.error("Unexpected error during control loop step: %s", e, exc_info=True)

        # Sleep with responsive interrupt handling
        sleep_until = time.time() + poll_interval
        while time.time() < sleep_until and not stop_requested:
            time.sleep(0.5)

    # Clean shutdown
    logger.info("Stopping services...")
    solaredge_client.close()
    logger.info("Service shutdown complete.")


if __name__ == "__main__":
    main()
