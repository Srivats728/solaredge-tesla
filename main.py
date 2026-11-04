"""
Main Service Runner for SolarEdge - Tesla Solar Charging Optimizer
Integrates autonomous background daemon loop with interactive Web Dashboard on port 8080.
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
import yaml

from solaredge import SolarEdgeClient
from tesla_client import TeslaController
from notifier import WhatsAppNotifier
from controller import SolarChargingController
from web.app import create_app

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
        if os.path.exists(example_path):
            import shutil
            shutil.copy(example_path, path)
        else:
            raise FileNotFoundError(f"Configuration file '{path}' not found!")

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_optimizer_loop(controller: SolarChargingController, poll_interval: int):
    """Background thread running the solar charging optimizer."""
    logger = logging.getLogger("optimizer")
    logger.info("Optimizer background loop started (interval: %ds).", poll_interval)

    while not stop_requested:
        try:
            # Check if Tesla is authorized before evaluating
            if not controller.tesla.vehicle:
                # Attempt lazy initialization if credentials were saved via Web Dashboard
                controller.tesla.initialize()

            controller.evaluate_step()
        except Exception as e:
            logger.error("Unexpected error during optimizer evaluation: %s", e, exc_info=True)

        # Sleep with responsive interrupt handling
        sleep_until = time.time() + poll_interval
        while time.time() < sleep_until and not stop_requested:
            time.sleep(0.5)

    logger.info("Optimizer loop ended.")


def main():
    global stop_requested
    parser = argparse.ArgumentParser(description="SolarEdge - Tesla Solar Charging Optimizer")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("-p", "--port", type=int, default=8080, help="Web Dashboard Port (default: 8080)")
    parser.add_argument("--no-web", action="store_true", help="Run without web dashboard")
    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

    setup_logging(config)
    logger = logging.getLogger("main")
    logger.info("================================================================")
    logger.info(" SolarEdge ↔ Tesla Solar Optimizer Service Starting...")
    logger.info("================================================================")

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

    # Initialize Tesla controller (Non-fatal if not yet authorized so Web UI can load!)
    tesla_cfg = config.get("tesla", {})
    tesla_client = TeslaController(
        email=tesla_cfg.get("email", ""),
        vin=tesla_cfg.get("vin")
    )
    if not tesla_client.initialize():
        logger.warning("Tesla account is not yet authorized.")
        logger.warning("Open the Web Dashboard at http://<IP>:%d to authenticate your account.", args.port)

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

    # Launch Optimizer in Background Thread
    opt_thread = threading.Thread(
        target=run_optimizer_loop,
        args=(controller, poll_interval),
        daemon=True,
        name="OptimizerWorker"
    )
    opt_thread.start()

    # Start Flask Web Dashboard
    if not args.no_web:
        app = create_app(controller_ref=controller, config_path=args.config)
        logger.info("Web Dashboard listening on http://0.0.0.0:%d", args.port)

        # Run web server (Werkzeug quiet logs)
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.WARNING)

        try:
            app.run(host="0.0.0.0", port=args.port, debug=False, use_reloader=False)
        except (KeyboardInterrupt, SystemExit):
            pass
    else:
        # If running headless without web server, wait on thread
        while not stop_requested:
            time.sleep(1)

    logger.info("Shutting down services...")
    solaredge_client.close()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
