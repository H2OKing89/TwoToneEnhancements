# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: Heartbeat Monitor
# Version: v1.7.0
# Author: Quentin King
# Date: 08-28-2024
# Description: Monitors the heartbeat of the TwoToneDetect system by checking for
#              updates in the heartbeat.log file. Handles graceful shutdowns, sends 
#              detailed alerts via a webhook and Pushover if the heartbeat fails, 
#              and features log rotation. Configurable via config.ini, pushover.ini, 
#              and heartbeat.ini files.
# Changelog:
# - v1.7.0: Updated to read from separate config.ini, pushover.ini, and heartbeat.ini files.
#           Enhanced error handling and updated logging configuration.
# - v1.6.1: Fixed an issue where the alert message incorrectly indicated "Shutdown due
#           to max retries reached" instead of "User-initiated shutdown" during user-
#           initiated shutdowns. Ensured proper handling of the `user_initiated` flag
#           in the `send_alert` function.
# - v1.6.0: Added distinction between user-initiated shutdowns and max retries shutdowns
#           in the alert messages. Updated `graceful_shutdown` and `send_alert` functions
#           to handle this. Included new parameter `user_initiated` in `send_alert`.
# - v1.5.1: Renamed log files to include more specific identifiers for each script/component.
#           Added detailed docstrings for all functions to improve code readability.
# - v1.5.0: Added detailed error handling, enhanced log formatting, user-initiated shutdown logging,
#           and improved robustness in checking the heartbeat file.
# -----------------------------------------------------------------------------

import os
import time
import requests
import configparser
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import socket
import sys
import argparse
import signal

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Load configuration from the ini files
config = configparser.ConfigParser()

# Assuming all INI files are in the same directory as this script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load config.ini, pushover.ini, and heartbeat.ini
config.read([
    os.path.join(script_dir, 'config.ini'),
    os.path.join(script_dir, 'pushover.ini'),
    os.path.join(script_dir, 'heartbeat.ini')
])

# Heartbeat monitoring configuration
heartbeat_file = config['Heartbeat']['file_path']
check_interval = int(config['Heartbeat']['check_interval'])
heartbeat_threshold = int(config['Heartbeat'].get('threshold', int(check_interval * 1.5)))
min_threshold = 60  # Minimum threshold to avoid overly sensitive alerts
heartbeat_threshold = max(heartbeat_threshold, min_threshold)

# Webhook configuration
webhook_url = config['Webhook']['heartbeat_url']

# Retry configuration
max_retries = int(config['Retries']['max_retries'])
retry_delay = int(config['Retries']['retry_delay'])

# Logging configuration
log_dir = os.path.join(script_dir, config['Logging']['log_dir'])
cleanup_days = int(config['Logging']['cleanup_days'])

# Pushover configuration
pushover_token = config['Pushover']['PUSHOVER_TOKEN']
pushover_user = config['Pushover']['PUSHOVER_USER']
pushover_priority = int(config['Pushover'].get('priority', 1))  # Configurable priority

# Set up logging with rotation and including timestamps
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Heartbeat log
heartbeat_log_file = os.path.join(log_dir, "ttd_heartbeat.log")
heartbeat_logger = logging.getLogger('heartbeat')
heartbeat_handler = RotatingFileHandler(heartbeat_log_file, maxBytes=1048576, backupCount=5)  # 1MB per log file
heartbeat_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
heartbeat_handler.setFormatter(heartbeat_formatter)
heartbeat_logger.addHandler(heartbeat_handler)
heartbeat_logger.setLevel(logging.INFO)

# Webhook log
webhook_log_file = os.path.join(log_dir, "ttd_heartbeat_webhook_alerts.log")
webhook_logger = logging.getLogger('webhook')
webhook_handler = RotatingFileHandler(webhook_log_file, maxBytes=1048576, backupCount=5)
webhook_handler.setFormatter(heartbeat_formatter)
webhook_logger.addHandler(webhook_handler)
webhook_logger.setLevel(logging.INFO)

# Pushover log
pushover_log_file = os.path.join(log_dir, "ttd_heartbeat_pushover_alerts.log")
pushover_logger = logging.getLogger('pushover')
pushover_handler = RotatingFileHandler(pushover_log_file, maxBytes=1048576, backupCount=5)
pushover_handler.setFormatter(heartbeat_formatter)
pushover_logger.addHandler(pushover_handler)
pushover_logger.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Function: cleanup_logs
# Description: Cleans up old log files in the specified directory that are older
#              than the specified number of days.
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
    for filename in os.listdir(log_dir):
        file_path = os.path.join(log_dir, filename)
        if os.path.isfile(file_path) and now - os.path.getmtime(file_path) > cleanup_days * 86400:
            os.remove(file_path)
            heartbeat_logger.info(f"Deleted old log file: {filename}")

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
            heartbeat_logger.info("Heartbeat detected.")
            return True

    except FileNotFoundError:
        heartbeat_logger.error(f"Heartbeat file not found: {heartbeat_file}")
        return False
    except ValueError:
        heartbeat_logger.error(f"Heartbeat file contains invalid data: {heartbeat_file}")
        return False
    except Exception as e:
        heartbeat_logger.error(f"Error checking heartbeat: {str(e)}")
        return False

# -----------------------------------------------------------------------------
# Function: send_alert
# Description: Sends a detailed notification to the configured webhook URL and 
#              Pushover service with the current status, retry count, and additional context.
# -----------------------------------------------------------------------------
def send_alert(retries, final=False, user_initiated=False):
    """
    Sends a notification to the configured webhook URL and Pushover service.

    This function sends a detailed notification containing the current status, 
    retry count, and additional context to both a webhook URL and Pushover 
    service. It logs the results of these actions.

    Args:
        retries (int): The current retry count.
        final (bool): If True, indicates that this is the final alert before shutdown.
        user_initiated (bool): If True, indicates that this is a user-initiated shutdown.

    Returns:
        None
    """
    last_heartbeat_time = datetime.fromtimestamp(os.path.getmtime(heartbeat_file)).strftime('%Y-%m-%d %H:%M:%S')
    if final:
        # Correctly handle user-initiated shutdown
        message = "User-initiated shutdown" if user_initiated else "Shutdown due to max retries reached"
    else:
        message = "Heartbeat not detected"
    
    payload = {
        "message": f"{message}. Last heartbeat at {last_heartbeat_time}.",
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "retries": retries,
        "hostname": socket.gethostname(),
        "threshold": heartbeat_threshold
    }
    
    # Send webhook notification
    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        webhook_logger.info("Alert sent successfully via webhook.")
    except requests.exceptions.ConnectionError:
        webhook_logger.error(f"Connection error while sending webhook alert.")
    except requests.exceptions.Timeout:
        webhook_logger.error(f"Timeout error while sending webhook alert.")
    except requests.exceptions.RequestException as e:
        webhook_logger.error(f"Failed to send webhook alert: {str(e)}")

    # Send Pushover notification
    try:
        pushover_data = {
            "token": pushover_token,
            "user": pushover_user,
            "message": payload["message"],
            "title": "Heartbeat Monitor Alert",
            "priority": pushover_priority  # Configurable priority
        }
        response = requests.post("https://api.pushover.net/1/messages.json", data=pushover_data)
        response.raise_for_status()
        pushover_logger.info("Alert sent successfully via Pushover.")
    except requests.exceptions.ConnectionError:
        pushover_logger.error(f"Connection error while sending Pushover alert.")
    except requests.exceptions.Timeout:
        pushover_logger.error(f"Timeout error while sending Pushover alert.")
    except requests.exceptions.RequestException as e:
        pushover_logger.error(f"Failed to send Pushover alert: {str(e)}")

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
    heartbeat_logger.info("User-initiated shutdown.")
    send_alert(retries=0, final=True, user_initiated=True)  # Final alert with shutdown message
    cleanup_logs()
    sys.exit(0)

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Handle graceful shutdown on interrupt signals
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    
    heartbeat_logger.info("Heartbeat monitor script started.")
    cleanup_logs()

    retries = 0
    while True:
        if check_heartbeat():
            retries = 0  # Reset retries if heartbeat is detected
        else:
            retries += 1
            if retries >= max_retries:
                heartbeat_logger.error("Max retries reached. Sending alert and shutting down.")
                send_alert(retries, final=True)
                graceful_shutdown(signal.SIGTERM, None)  # Trigger graceful shutdown
                break
            else:
                backoff_time = retry_delay * (2 ** (retries - 1))  # Exponential backoff
                heartbeat_logger.warning(f"Retrying... ({retries}/{max_retries}) in {backoff_time} seconds.")
                time.sleep(backoff_time)

        time.sleep(check_interval)
    heartbeat_logger.info("Heartbeat monitor script stopped.")
