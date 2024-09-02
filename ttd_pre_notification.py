import configparser
import os
import logging
from logging.handlers import RotatingFileHandler
import requests
import sys
import argparse
from time import sleep
from datetime import datetime

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_pre_notification.py
# Version: v1.7.9
# Author: Quentin King
# Date: 09-01-2024
# Description: This script sends a pre-notification webhook to Node-RED with 
#              the audio file URL and relevant details. It includes error 
#              handling, Pushover notifications for failures, and retry 
#              mechanisms with exponential backoff. Configuration settings are 
#              loaded from shared INI files for flexibility and ease of use.
# Changelog:
# - v1.7.9: Fixed issue where cleanup_logs() was called before logging was configured, 
#           causing an AttributeError. Moved cleanup_logs() call after logging setup.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Get the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))

# Define relative paths to the config.ini and credentials.ini files
config_path = os.path.join(script_dir, 'config.ini')
credentials_path = os.path.join(script_dir, 'credentials.ini')

# Load configuration from the config.ini and credentials.ini files
config = configparser.ConfigParser()
config.read([config_path, credentials_path])

# Access the Logging configuration
log_dir = os.path.join(script_dir, config['ttd_pre_notification_Logging']['log_dir'])
log_level = config['ttd_pre_notification_Logging']['log_level']
max_logs = int(config['ttd_pre_notification_Logging']['max_logs'])
max_log_size = int(config['ttd_pre_notification_Logging']['max_log_size'])
log_to_console = config.getboolean('ttd_pre_notification_Logging', 'log_to_console')
verbose_logging = config.getboolean('ttd_pre_notification_Logging', 'verbose_logging')
max_log_days = int(config['ttd_pre_notification_Logging']['max_log_days'])

# Ensure the log directory exists
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure rotating file handler for logging
log_file_name = f"pre_notification_{datetime.now().strftime('%m-%d-%Y_%H-%M-%S')}.log"
log_file_path = os.path.join(log_dir, log_file_name)

handler = RotatingFileHandler(
    log_file_path, maxBytes=max_log_size, backupCount=max_logs
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG level
    handlers=[handler]
)

if log_to_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized.")
logging.info(f"Logs will be stored in: {log_dir}")
logging.info(f"Log file: {log_file_name}")

# -----------------------------------------------------------------------------
# Log Cleanup Function
# -----------------------------------------------------------------------------
def cleanup_logs():
    """
    Cleans up old log files in the specified directory based on age and the number of logs.
    This function deletes log files that are older than the 'max_log_days' configuration
    parameter or if the total number of logs exceeds the 'max_logs' limit.
    
    Returns:
        None
    """
    logging.debug("Entered cleanup_logs function.")
    now = datetime.now().timestamp()
    logs = []
    
    # Gather all logs and their ages
    for filename in os.listdir(log_dir):
        file_path = os.path.join(log_dir, filename)
        if os.path.isfile(file_path):
            file_age = now - os.path.getmtime(file_path)
            logs.append((file_path, file_age))
    
    # Sort logs by age (oldest first)
    logs.sort(key=lambda x: x[1], reverse=False)
    
    # Get the current log file path to avoid deletion
    current_log_file = log_file_path

    # Delete logs based on age
    deleted_files_count = 0
    for file_path, file_age in logs:
        if file_path == current_log_file:
            logging.debug(f"Skipping current log file: {os.path.basename(file_path)}")
            continue
        if file_age > max_log_days * 86400:
            os.remove(file_path)
            logging.info(f"Deleted old log file: {os.path.basename(file_path)}")
            deleted_files_count += 1
    
    # Re-evaluate logs after age-based cleanup
    logs = [(fp, fa) for fp, fa in logs if os.path.exists(fp)]
    
    # If number of logs exceeds max_logs, delete the oldest ones
    if len(logs) > max_logs:
        logs_to_delete = len(logs) - max_logs
        for i in range(logs_to_delete):
            if logs[i][0] == current_log_file:
                logging.debug(f"Skipping current log file: {os.path.basename(logs[i][0])}")
                continue
            os.remove(logs[i][0])
            logging.info(f"Deleted excess log file: {os.path.basename(logs[i][0])}")
            deleted_files_count += 1
    
    if deleted_files_count == 0:
        logging.info("No old or excess log files were found for deletion.")
    else:
        logging.info(f"Deleted {deleted_files_count} old or excess log file(s).")
    
    logging.debug("Exiting cleanup_logs function.")

# Now that logging is configured, run cleanup
cleanup_logs()

# -----------------------------------------------------------------------------
# Access other configurations
# -----------------------------------------------------------------------------
# Access the Pushover credentials and settings
logging.debug("Loading Pushover settings.")
pushover_app_token = config['ttd_pre_notification_Pushover']['pushover_token']
pushover_user_key = config['ttd_pre_notification_Pushover']['pushover_user']
pushover_priority = int(config['ttd_pre_notification_Pushover']['priority'])
pushover_retry = int(config['ttd_pre_notification_Pushover']['retry'])
pushover_expire = int(config['ttd_pre_notification_Pushover']['expire'])
pushover_sound = config['ttd_pre_notification_Pushover']['sound']
logging.info("Pushover settings loaded.")

# Access the Webhook and base audio URL
logging.debug("Loading Webhook settings.")
webhook_url = config['ttd_pre_notification_Webhook']['tone_detected_url']
base_audio_url = config['ttd_pre_notification_Webhook']['base_audio_url']
secondary_webhook_url = config['ttd_pre_notification_Webhook']['secondary_webhook_url']
timeout_seconds = int(config['ttd_pre_notification_Webhook']['timeout_seconds'])
logging.info("Webhook settings loaded.")

# Access the Retry logic settings
logging.debug("Loading Retry logic settings.")
max_retries = int(config['ttd_pre_notification_Retry']['max_retries'])
initial_backoff = int(config['ttd_pre_notification_Retry']['initial_backoff'])
backoff_multiplier = int(config['ttd_pre_notification_Retry']['backoff_multiplier'])
logging.info("Retry logic settings loaded.")

# Access the File Handling settings
logging.debug("Loading File Handling settings.")
temp_directory = os.path.join(script_dir, config['ttd_pre_notification_FileHandling']['temp_directory'])
file_name_format = config['ttd_pre_notification_FileHandling']['file_name_format']
# Ensure the temp directory exists
if not os.path.exists(temp_directory):
    os.makedirs(temp_directory)
logging.info(f"Temporary files will be stored in: {temp_directory}")

# Access the Notification Content settings
logging.debug("Loading Notification Content settings.")
title_prefix = config['ttd_pre_notification_NotificationContent']['title_prefix']
message_template = config['ttd_pre_notification_NotificationContent']['message_template']
logging.info("Notification content settings loaded.")

# -----------------------------------------------------------------------------
# Function: send_webhook
# Description: Sends a webhook to Node-RED with retry mechanism and tailored 
#              exception handling. Uses exponential backoff for retries.
# -----------------------------------------------------------------------------
def send_webhook(file_name, topic, retries=max_retries):
    """
    Sends a webhook to Node-RED with the audio file URL and relevant details.

    This function attempts to send a webhook with the specified audio file and 
    topic. If the request fails, it retries up to the specified number of times, 
    using exponential backoff.

    Args:
        file_name (str): The name of the audio file to be included in the webhook.
        topic (str): The topic for the webhook and notification.
        retries (int): Number of retry attempts for sending the webhook (default is max_retries).

    Returns:
        bool: True if the webhook was sent successfully, False otherwise.
    """
    logging.debug("Entered send_webhook function.")
    formatted_file_name = os.path.basename(file_name)  # Extract the file name
    file_url = f"{base_audio_url}{formatted_file_name}"  # Construct the full URL
    payload = {
        "payload": {
            "message": file_url,
            "title": topic,
            "topic": topic
        }
    }

    logging.info(f"Webhook payload: {payload}")

    attempt = 0
    backoff_time = initial_backoff

    while attempt < retries:
        try:
            logging.info(f"Attempt {attempt + 1} to send webhook.")
            response = requests.post(webhook_url, json=payload, timeout=timeout_seconds)
            response.raise_for_status()  # Raise an HTTPError for bad responses
            logging.info(f"Webhook sent successfully: {payload}")
            logging.debug("Exiting send_webhook function after success.")
            return True

        except requests.exceptions.ConnectionError as conn_err:
            logging.error(f"Attempt {attempt + 1}: Connection Error: {conn_err}")
            if attempt < retries - 1:
                logging.info("Retrying immediately due to connection error...")
                sleep(1)
            else:
                logging.error("Max retries reached for connection error.")
        
        except requests.exceptions.Timeout as timeout_err:
            logging.error(f"Attempt {attempt + 1}: Timeout Error: {timeout_err}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to timeout...")
                sleep(backoff_time)
                backoff_time *= backoff_multiplier
        
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"Attempt {attempt + 1}: HTTP Error: {http_err}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to HTTP error...")
                sleep(backoff_time)
                backoff_time *= backoff_multiplier
        
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Attempt {attempt + 1}: General Webhook Error: {req_err}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to general error...")
                sleep(backoff_time)
                backoff_time *= backoff_multiplier

        attempt += 1

    logging.error("Webhook failed after all retry attempts.")
    logging.debug("Exiting send_webhook function after failure.")
    return False

# -----------------------------------------------------------------------------
# Function: send_error_notification
# Description: Sends a Pushover notification for errors encountered during the
#              webhook process.
# -----------------------------------------------------------------------------
def send_error_notification(error_message):
    """
    Sends a Pushover notification for errors encountered during the webhook process.

    This function sends a Pushover notification with the specified error message. 
    It is used when a webhook fails to send after the configured number of retries.

    Args:
        error_message (str): The error message to be included in the Pushover notification.

    Returns:
        None
    """
    logging.debug("Entered send_error_notification function.")
    pushover_url = "https://api.pushover.net/1/messages.json"
    pushover_data = {
        "token": pushover_app_token,
        "user": pushover_user_key,
        "message": error_message,
        "priority": pushover_priority,
        "retry": pushover_retry,
        "expire": pushover_expire,
        "sound": pushover_sound
    }
    try:
        response = requests.post(pushover_url, data=pushover_data)
        response.raise_for_status()
        logging.info("Pushover notification sent successfully.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Pushover notification: {e}")
    logging.debug("Exiting send_error_notification function.")

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
def main():
    """
    Main function to parse arguments and initiate the webhook process.

    This function parses command-line arguments to extract the audio file name 
    and topic, then calls send_webhook() to send the information to Node-RED.
    """
    logging.debug("Entered main function.")
    parser = argparse.ArgumentParser(description="Send a webhook to Node-RED with audio file details.")
    parser.add_argument('file_name', help="The name of the audio file.")
    parser.add_argument('topic', help="The topic for the webhook and notification.")
    parser.add_argument('--retries', type=int, default=max_retries, help="Number of retry attempts for sending the webhook.")
    
    args = parser.parse_args()

    logging.info(f"Received arguments: {args}")
    logging.info(f"Sending webhook for file: {args.file_name} with topic: {args.topic}")

    if not send_webhook(args.file_name, args.topic, args.retries):
        logging.error("Failed to send webhook after multiple attempts.")

    logging.debug("Exiting main function.")

if __name__ == "__main__":
    main()
