import os
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import subprocess
import requests
import configparser
import signal
import sys

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: Heartbeat Monitor and Restart with Notifications
# Version: v1.5.0
# Author: Quentin King
# Date: 08-31-2024
# Description: This script monitors a heartbeat file for updates. If the heartbeat
#              is not detected within the expected threshold, it attempts to restart
#              the monitored program by executing an external Python script (start.py).
#              The script sends notifications via Pushover and a webhook if the heartbeat
#              fails or if the restart attempt occurs. Additional features include:
#              - Retry logic for starting the external script
#              - Graceful shutdown handling
#              - Rate limiting for notifications (with exceptions for success notifications)
#              - Detailed logging, including audit logs for significant events
#              - Configurable via an external config.ini file
#              - Log cleanup to remove old log files based on age
# -----------------------------------------------------------------------------
# Changelog:
# - v1.5.0:
#   - Updated to integrate with the new INI configuration setup, including max_logs and log retention.
#   - Enhanced the logging system with per-script log directories.
#   - Improved error handling and rate limiting for notifications.
# - v1.4.0:
#   - Enhanced logging levels, using DEBUG for verbose logs, INFO for general operations,
#     WARNING for potential issues, ERROR for significant problems, and CRITICAL for severe issues.
#   - Improved error handling in check_heartbeat and start_external_script functions,
#     with more granular exceptions and detailed error logging.
#   - Added a requirements.txt file for dependency management.
# -----------------------------------------------------------------------------
# Configuration:
# - `Heartbeat` section in config.ini:
#   - `file_path`: Path to the heartbeat file being monitored.
#   - `check_interval`: Interval (in seconds) between heartbeat checks.
#   - `threshold`: Maximum allowable time difference (in seconds) between the current time
#                  and the last heartbeat update before considering it a failure.
# - `Logging` section in config.ini:
#   - `log_dir`: Directory where logs will be stored.
#   - `cleanup_days`: Number of days after which old log files should be deleted.
#   - `max_logs`: Maximum number of log files to keep.
# - `Pushover` section in config.ini:
#   - `token`: Pushover API token for sending notifications.
#   - `user`: Pushover user key for sending notifications.
#   - `priority`: Priority level for Pushover notifications.
# - `Webhook` section in config.ini:
#   - `heartbeat_url`: Webhook URL to send heartbeat failure notifications.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Load configuration from the config.ini file
config = configparser.ConfigParser()
script_dir = os.path.dirname(os.path.abspath(__file__))
config.read(os.path.join(script_dir, 'config.ini'))

# Heartbeat monitoring configuration
heartbeat_file = config['Heartbeat']['file_path']
check_interval = int(config['Heartbeat']['check_interval'])
heartbeat_threshold = int(config['Heartbeat'].get('threshold', int(check_interval * 1.5)))
min_threshold = 60  # Minimum threshold to avoid overly sensitive alerts
heartbeat_threshold = max(heartbeat_threshold, min_threshold)

# External script to start the program
external_script = os.path.join(script_dir, 'start.py')

# Logging configuration
log_dir = os.path.join(script_dir, config['Logging']['log_dir'])
cleanup_days = int(config['Logging'].get('cleanup_days', 7))  # Default to 7 days if not specified
max_logs = int(config['Logging'].get('max_logs', 10))  # Default to 10 logs if not specified

# Pushover configuration
pushover_token = config['Pushover']['token']
pushover_user = config['Pushover']['user']
pushover_priority = int(config['Pushover'].get('priority', 1))  # Configurable priority

# Webhook configuration
webhook_url = config['Webhook']['heartbeat_url']

# Set up logging with rotation and including timestamps
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Heartbeat log
heartbeat_log_file = os.path.join(log_dir, "heartbeat_monitor.log")
heartbeat_logger = logging.getLogger('heartbeat_monitor')
heartbeat_handler = RotatingFileHandler(heartbeat_log_file, maxBytes=1048576, backupCount=max_logs)
heartbeat_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
heartbeat_handler.setFormatter(heartbeat_formatter)
heartbeat_logger.addHandler(heartbeat_handler)
heartbeat_logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture all levels of logs

# Audit log
audit_log_file = os.path.join(log_dir, "heartbeat_audit.log")
audit_logger = logging.getLogger('audit')
audit_handler = RotatingFileHandler(audit_log_file, maxBytes=1048576, backupCount=max_logs)
audit_handler.setFormatter(heartbeat_formatter)
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)  # Audit logs should be more concise, starting at INFO level

# -----------------------------------------------------------------------------
# Function: check_heartbeat
# Description: Checks the heartbeat log file for updates. If the last update
#              time exceeds the heartbeat threshold, it returns False.
# Returns: True if heartbeat is within the threshold, False otherwise.
# -----------------------------------------------------------------------------
def check_heartbeat():
    """
    Checks the heartbeat log file for updates.

    This function reads the last update time from the heartbeat file and 
    compares it with the current time. If the difference exceeds the 
    'heartbeat_threshold', it logs a warning and returns False.

    Returns:
        bool: True if the heartbeat is within the threshold, False otherwise.

    Raises:
        FileNotFoundError: If the heartbeat file is not found.
        ValueError: If the heartbeat file contains invalid data.
    """
    try:
        with open(heartbeat_file, 'r') as file:
            last_heartbeat = int(float(file.read().strip()))  # Handle float conversion if needed
        current_time = int(time.time())
        time_diff = current_time - last_heartbeat

        if time_diff > heartbeat_threshold:
            heartbeat_logger.warning(f"No heartbeat detected. Last heartbeat was {time_diff} seconds ago.")
            return False
        else:
            heartbeat_logger.debug("Heartbeat detected.")
            return True

    except FileNotFoundError:
        heartbeat_logger.error(f"Heartbeat file not found: {heartbeat_file}")
        return False
    except ValueError:
        heartbeat_logger.error(f"Heartbeat file contains invalid data: {heartbeat_file}")
        return False
    except Exception as e:
        heartbeat_logger.critical(f"Critical error checking heartbeat: {str(e)}", exc_info=True)
        return False

# -----------------------------------------------------------------------------
# Function: send_alert
# Description: Sends a notification to Pushover and a webhook with the current status.
# -----------------------------------------------------------------------------
last_alert_time = None

def send_alert(message, relaunching=False, relaunch_success=False):
    """
    Sends a notification to the configured webhook URL and Pushover service.

    This function sends a detailed notification containing the current status,
    retry count, and additional context to both a webhook URL and Pushover 
    service. It logs the results of these actions.

    Args:
        message (str): The message to be sent in the notification.
        relaunching (bool): If True, indicates that the program is attempting to relaunch.
        relaunch_success (bool): If True, indicates that the program was successfully relaunched.

    Returns:
        None
    """
    global last_alert_time
    current_time = time.time()

    # Check if rate limiting should be applied
    apply_rate_limit = not relaunch_success  # Only apply rate limiting if it's not a success notification

    if not apply_rate_limit or (last_alert_time is None or (current_time - last_alert_time) > 300):  # 5-minute cooldown
        last_alert_time = current_time
        # Add timestamp to the message
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        full_message = f"{timestamp} - {message}"

        # Send webhook notification
        try:
            payload = {"message": full_message}
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
            heartbeat_logger.info("Alert sent successfully via webhook.")
        except requests.exceptions.RequestException as e:
            heartbeat_logger.error(f"Failed to send webhook alert: {str(e)}")

        # Send Pushover notification
        try:
            pushover_data = {
                "token": pushover_token,
                "user": pushover_user,
                "message": full_message,
                "title": "Heartbeat Monitor Alert",
                "priority": pushover_priority
            }
            response = requests.post("https://api.pushover.net/1/messages.json", data=pushover_data)
            response.raise_for_status()
            heartbeat_logger.info("Alert sent successfully via Pushover.")
        except requests.exceptions.RequestException as e:
            heartbeat_logger.error(f"Failed to send Pushover alert: {str(e)}")
    else:
        heartbeat_logger.info("Alert suppressed due to rate limiting.")

# -----------------------------------------------------------------------------
# Function: start_external_script
# Description: Starts the external Python script if the heartbeat is not detected.
#              Logs detailed error information if the script fails to run.
# -----------------------------------------------------------------------------
def start_external_script():
    """
    Starts the external Python script to restart the monitored program.

    Logs detailed error information if the script fails to run.
    """
    retries = 3
    for attempt in range(retries):
        try:
            # Execute the external Python script
            command = f'python "{external_script}"'
            heartbeat_logger.debug(f"Executing command: {command}")  # DEBUG level for more details

            process = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Log the results (including DEBUG level for stdout and stderr)
            heartbeat_logger.debug(f"Return code: {process.returncode}")
            heartbeat_logger.debug(f"Stdout: {process.stdout}")
            if process.stderr:
                heartbeat_logger.error(f"Stderr: {process.stderr}")

            if process.returncode == 0:
                heartbeat_logger.info(f"Successfully executed the script: {external_script}")
                send_alert("Program successfully restarted.", relaunch_success=True)
                audit_logger.info(f"Program restarted successfully after {attempt + 1} attempt(s).")
                break  # Exit loop on success
            else:
                heartbeat_logger.error(f"Failed to execute the script: {external_script}")
                send_alert("Failed to restart the program.", relaunching=True)

        except subprocess.CalledProcessError as e:
            heartbeat_logger.error(f"Subprocess error while executing the script: {str(e)}", exc_info=True)
            send_alert(f"Subprocess error: {str(e)}")
        except Exception as e:
            heartbeat_logger.critical(f"Unexpected critical error while attempting to execute the script: {str(e)}", exc_info=True)
            send_alert(f"Unexpected critical error: {str(e)}")

        if attempt < retries - 1:
            heartbeat_logger.info(f"Retrying script execution (Attempt {attempt + 2}/{retries})...")
            time.sleep(5)  # Wait before retrying

# -----------------------------------------------------------------------------
# Function: graceful_shutdown
# Description: Handles graceful shutdown by sending a final alert and performing cleanup.
# -----------------------------------------------------------------------------
def graceful_shutdown(signal_received, frame):
    """
    Handles the graceful shutdown of the script.

    This function sends a final alert, performs any necessary cleanup tasks, 
    and exits the script.

    Args:
        signal_received: The signal received that triggered the shutdown.
        frame: The current stack frame.

    Returns:
        None
    """
    heartbeat_logger.info("Graceful shutdown initiated.")
    audit_logger.info("Heartbeat monitor is shutting down.")
    send_alert("Heartbeat monitor is shutting down.")
    sys.exit(0)

# -----------------------------------------------------------------------------
# Function: cleanup_logs
# Description: Deletes old log files that exceed the defined retention period.
# -----------------------------------------------------------------------------
def cleanup_logs():
    """
    Cleans up old log files in the specified directory that are older than the 
    configured number of days.

    This function deletes log files that are older than the 'cleanup_days' 
    configuration parameter from the 'log_dir' directory.

    Returns:
        None
    """
    now = time.time()
    logs = sorted(os.listdir(log_dir))
    for filename in logs:
        file_path = os.path.join(log_dir, filename)
        if os.path.isfile(file_path) and now - os.path.getmtime(file_path) > cleanup_days * 86400:
            os.remove(file_path)
            heartbeat_logger.info(f"Deleted old log file: {filename}")
            audit_logger.info(f"Deleted old log file: {filename}")

    # Re-sort logs after deleting old ones
    logs = sorted(os.listdir(log_dir))

    # Delete logs based on the maximum number of files
    while len(logs) > max_logs:
        oldest_log = logs.pop(0)
        os.remove(os.path.join(log_dir, oldest_log))
        heartbeat_logger.info(f"Deleted old log file based on count: {oldest_log}")
        audit_logger.info(f"Deleted old log file based on count: {oldest_log}")

# -----------------------------------------------------------------------------
# Main Execution Loop
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Handle graceful shutdown on interrupt signals
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    heartbeat_logger.info("Heartbeat Monitor started.")
    audit_logger.info("Heartbeat monitor script started.")

    # Perform initial log cleanup
    cleanup_logs()

    while True:
        if not check_heartbeat():
            heartbeat_logger.warning("Heartbeat not detected. Attempting to start the external script.")
            send_alert("Heartbeat not detected. Attempting to restart the program.", relaunching=True)
            start_external_script()
            time.sleep(check_interval)  # Wait for the check interval before the next iteration

        time.sleep(check_interval)

        # Perform periodic log cleanup
        cleanup_logs()
