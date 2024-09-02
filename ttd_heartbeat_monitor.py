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
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_heartbeat_monitor.py
# Version: v1.5.0
# Author: Quentin King
# Date: 09-01-2024
# Description: This script monitors a heartbeat file for updates. If the heartbeat
#              is not detected within the expected threshold, it attempts to restart
#              the monitored program by executing an external Python script (start.py).
#              The script sends notifications via Pushover and a webhook if the heartbeat
#              fails or if the restart attempt occurs.
# Changelog:
# - v1.5.0: Moved sensitive credentials to environment variables, updated logging 
#           and audit logging, and ensured all settings from config.ini are used.
# - v1.4.2: Implemented audit logging and feature toggles. Verified all settings from config.ini are used.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Load Environment Variables
# -----------------------------------------------------------------------------
load_dotenv()

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Get the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))

# Define the path to the config.ini file
config_path = os.path.join(script_dir, 'config.ini')

# Load configuration with interpolation disabled for logging section
config = configparser.ConfigParser(interpolation=None)
config.read(config_path)

# Access the Logging configuration
log_dir = os.path.join(script_dir, config['ttd_heartbeat_Logging']['log_dir'])
log_level = config['ttd_heartbeat_Logging']['log_level']
log_format = config['ttd_heartbeat_Logging']['log_format']
log_to_console = config.getboolean('ttd_heartbeat_Logging', 'log_to_console')
max_log_days = int(config['ttd_heartbeat_Logging']['max_log_days'])

# Ensure the log directory exists
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logging
log_file_name = f"heartbeat_monitor_{datetime.now().strftime('%m-%d-%Y_%H-%M-%S')}.log"
log_file_path = os.path.join(log_dir, log_file_name)

logging.basicConfig(
    filename=log_file_path,
    level=getattr(logging, log_level.upper(), logging.DEBUG),
    format=log_format
)

if log_to_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
    console_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized.")
logging.info(f"Logs will be stored in: {log_dir}")
logging.info(f"Log file: {log_file_name}")

# Access the Heartbeat monitoring configuration
heartbeat_file = config['Heartbeat']['file_path']
check_interval = int(config['Heartbeat']['check_interval'])
heartbeat_threshold = int(config['Heartbeat'].get('threshold', int(check_interval * 1.5)))
min_threshold = 60  # Minimum threshold to avoid overly sensitive alerts
heartbeat_threshold = max(heartbeat_threshold, min_threshold)

# External script to start the program
external_script = config['Restart_Path']['file_path']

# Access the Pushover credentials and settings from environment variables
pushover_token = os.getenv('PUSHOVER_TOKEN')
pushover_user = os.getenv('PUSHOVER_USER')
pushover_priority = int(config['ttd_heartbeat_Pushover']['priority'])
pushover_retry = int(config['ttd_heartbeat_Pushover']['retry'])
pushover_expire = int(config['ttd_heartbeat_Pushover']['expire'])
pushover_sound = config['ttd_heartbeat_Pushover']['sound']

# Access the Webhook configuration
webhook_url = config['Webhook']['heartbeat_url']

# Access the Feature Toggles
enable_restart_notifications = config.getboolean('ttd_heartbeat_Features', 'enable_restart_notifications')
enable_rate_limiting = config.getboolean('ttd_heartbeat_Features', 'enable_rate_limiting')

# Access the Audit Logging configuration
audit_log_dir = os.path.join(script_dir, config['ttd_heartbeat_AuditLogging']['audit_log_dir'])
audit_log_level = config['ttd_heartbeat_AuditLogging']['audit_log_level']

# Ensure the audit log directory exists
if not os.path.exists(audit_log_dir):
    os.makedirs(audit_log_dir)

# Configure audit logging
audit_log_file_name = f"audit_{datetime.now().strftime('%m-%d-%Y_%H-%M-%S')}.log"
audit_log_file_path = os.path.join(audit_log_dir, audit_log_file_name)

audit_logger = logging.getLogger('audit')
audit_logger.setLevel(getattr(logging, audit_log_level.upper(), logging.INFO))
audit_handler = RotatingFileHandler(audit_log_file_path, maxBytes=1048576, backupCount=5)
audit_handler.setFormatter(logging.Formatter(log_format))
audit_logger.addHandler(audit_handler)

logging.info("Audit logging initialized.")
audit_logger.info(f"Audit log file: {audit_log_file_name}")

# -----------------------------------------------------------------------------
# Function: send_pushover_notification
# Description: Sends a notification to Pushover in case of critical errors.
# -----------------------------------------------------------------------------
def send_pushover_notification(message, additional_info=None):
    """
    Sends a Pushover notification for critical errors with additional context.

    Args:
        message (str): The error message to be sent via Pushover.
        additional_info (str, optional): Additional context or details about the error.

    Returns:
        None
    """
    pushover_url = "https://api.pushover.net/1/messages.json"
    full_message = f"{message}\nDetails: {additional_info}" if additional_info else message
    payload = {
        "token": pushover_token,
        "user": pushover_user,
        "message": full_message,
        "title": "Heartbeat Monitor Alert",
        "priority": pushover_priority,
        "retry": pushover_retry,
        "expire": pushover_expire,
        "sound": pushover_sound
    }
    try:
        response = requests.post(pushover_url, data=payload)
        response.raise_for_status()
        logging.info("Pushover notification sent successfully.")
        audit_logger.info(f"Pushover notification sent: {full_message}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Pushover notification: {e}")
        audit_logger.error(f"Failed to send Pushover notification: {e}")

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
            logging.warning(f"No heartbeat detected. Last heartbeat was {time_diff} seconds ago.")
            audit_logger.warning(f"No heartbeat detected. Last heartbeat was {time_diff} seconds ago.")
            return False
        else:
            logging.debug("Heartbeat detected.")
            audit_logger.debug("Heartbeat detected.")
            return True

    except FileNotFoundError:
        logging.error(f"Heartbeat file not found: {heartbeat_file}")
        audit_logger.error(f"Heartbeat file not found: {heartbeat_file}")
        return False
    except ValueError:
        logging.error(f"Heartbeat file contains invalid data: {heartbeat_file}")
        audit_logger.error(f"Heartbeat file contains invalid data: {heartbeat_file}")
        return False
    except Exception as e:
        logging.critical(f"Critical error checking heartbeat: {str(e)}", exc_info=True)
        audit_logger.critical(f"Critical error checking heartbeat: {str(e)}", exc_info=True)
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
    apply_rate_limit = enable_rate_limiting and not relaunch_success

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
            logging.info("Alert sent successfully via webhook.")
            audit_logger.info(f"Alert sent via webhook: {full_message}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send webhook alert: {str(e)}")
            audit_logger.error(f"Failed to send webhook alert: {str(e)}")

        # Send Pushover notification
        send_pushover_notification(full_message)
    else:
        logging.info("Alert suppressed due to rate limiting.")
        audit_logger.info("Alert suppressed due to rate limiting.")

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
            logging.debug(f"Executing command: {command}")  # DEBUG level for more details
            audit_logger.debug(f"Executing command: {command}")

            process = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Log the results (including DEBUG level for stdout and stderr)
            logging.debug(f"Return code: {process.returncode}")
            logging.debug(f"Stdout: {process.stdout}")
            audit_logger.debug(f"Return code: {process.returncode}")
            audit_logger.debug(f"Stdout: {process.stdout}")
            if process.stderr:
                logging.error(f"Stderr: {process.stderr}")
                audit_logger.error(f"Stderr: {process.stderr}")

            if process.returncode == 0:
                logging.info(f"Successfully executed the script: {external_script}")
                audit_logger.info(f"Successfully executed the script: {external_script}")
                if enable_restart_notifications:
                    send_alert("Program successfully restarted.", relaunch_success=True)
                break  # Exit loop on success
            else:
                logging.error(f"Failed to execute the script: {external_script}")
                audit_logger.error(f"Failed to execute the script: {external_script}")
                if enable_restart_notifications:
                    send_alert("Failed to restart the program.", relaunching=True)

        except subprocess.CalledProcessError as e:
            logging.error(f"Subprocess error while executing the script: {str(e)}", exc_info=True)
            audit_logger.error(f"Subprocess error while executing the script: {str(e)}", exc_info=True)
            send_alert(f"Subprocess error: {str(e)}")
        except Exception as e:
            logging.critical(f"Unexpected critical error while attempting to execute the script: {str(e)}", exc_info=True)
            audit_logger.critical(f"Unexpected critical error while attempting to execute the script: {str(e)}", exc_info=True)
            send_alert(f"Unexpected critical error: {str(e)}")

        if attempt < retries - 1:
            logging.info(f"Retrying script execution (Attempt {attempt + 2}/{retries})...")
            audit_logger.info(f"Retrying script execution (Attempt {attempt + 2}/{retries})...")
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
    shutdown_message = config['ttd_heartbeat_Shutdown']['shutdown_message']
    logging.info("Graceful shutdown initiated.")
    audit_logger.info("Graceful shutdown initiated.")
    send_alert(shutdown_message)
    if config.getboolean('ttd_heartbeat_Shutdown', 'perform_cleanup'):
        cleanup_logs()
    sys.exit(0)

# -----------------------------------------------------------------------------
# Function: cleanup_logs
# Description: Deletes old log files that exceed the defined retention period.
# -----------------------------------------------------------------------------
def cleanup_logs():
    """
    Cleans up old log files in the specified directory that are older than the 
    configured number of days.

    This function deletes log files that are older than the 'max_log_days' 
    configuration parameter from the 'log_dir' directory.

    Returns:
        None
    """
    now = time.time()
    for filename in os.listdir(log_dir):
        file_path = os.path.join(log_dir, filename)
        if os.path.isfile(file_path) and now - os.path.getmtime(file_path) > max_log_days * 86400:
            os.remove(file_path)
            logging.info(f"Deleted old log file: {filename}")
            audit_logger.info(f"Deleted old log file: {filename}")

# -----------------------------------------------------------------------------
# Main Execution Loop
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Handle graceful shutdown on interrupt signals
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    logging.info("Heartbeat Monitor started.")
    audit_logger.info("Heartbeat Monitor started.")

    # Perform initial log cleanup
    cleanup_logs()

    while True:
        if not check_heartbeat():
            logging.warning("Heartbeat not detected. Attempting to start the external script.")
            audit_logger.warning("Heartbeat not detected. Attempting to start the external script.")
            send_alert("Heartbeat not detected. Attempting to restart the program.", relaunching=True)
            start_external_script()
            time.sleep(check_interval)  # Wait for the check interval before the next iteration

        time.sleep(check_interval)

        # Perform periodic log cleanup
        cleanup_logs()
