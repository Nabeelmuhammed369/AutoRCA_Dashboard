import yaml
import logging

from Core.logger import setup_logger
from Monitors.api_monitor import check_api_health
from Monitors.log_analyzer import analyze_logs
from Monitors.db_validator import validate_data
from Core.rca_engine import classify_issue
from Core.reporter import generate_report

# Initialize logger
setup_logger()
logger = logging.getLogger("MAIN")

def run_autorca():
    try:
        logger.info("Starting AutoRCA engine...")

        with open("config.yaml") as f:
            config = yaml.safe_load(f)

        api_result = check_api_health(
            config["api"]["url"],
            config["api"]["timeout"]
        )

        log_result = analyze_logs(
            config["log"]["file"]
        )

        db_result = validate_data(
            config["database"]["path"]
        )

        classification = classify_issue(
            api_result,
            log_result,
            db_result
        )

        generate_report(api_result, log_result, db_result, classification)

        logger.info("AutoRCA execution completed.")

    except Exception:
        logger.exception("Critical failure in AutoRCA engine.")

if __name__ == "__main__":
    run_autorca()