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
# Version: v1.7.3
# Author: Quentin King
# Date: 09-01-2024
# Description: This script sends a pre-notification webhook to Node-RED with 
#              the audio file URL and relevant details. It includes error 
#              handling, Pushover notifications for failures, and retry 
#              mechanisms with exponential backoff. Configuration settings are 
#              loaded from shared INI files for flexibility and ease of use.
# Changelog:
# - v1.7.3: Updated log naming to match the desired format, ensured all logs 
#           are rotated correctly, and removed outdated log files exceeding 
#           the retention period. Added log cleanup to delete old logs.
# - v1.7.2: Added extensive logging for debugging, ensured paths for logs and 
#           temp files are relative to the script's directory.
# - v1.7.1: Updated paths for config.ini and credentials.ini to be relative 
#           to the script's directory, improving portability.
# - v1.7.0: Moved additional variables to config.ini, added log levels, 
#           included all adjustable Pushover settings, and reorganized 
#           configuration files to improve usability.
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
max_log_days = int(config['ttd_pre_notification_Logging'].get('max_log_days', 10))

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
    level=getattr(logging, log_level.upper(), logging.DEBUG),
    handlers=[handler]
)

if log_to_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
    console_handler.setFormatter(formatter)
    logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized.")
logging.info(f"Logs will be stored in: {log_dir}")
logging.info(f"Log file: {log_file_name}")

# Access the Pushover credentials and settings
pushover_app_token = config['ttd_pre_notification_Credentials']['pushover_token']
pushover_user_key = config['ttd_pre_notification_Credentials']['pushover_user']
pushover_priority = int(config['ttd_pre_notification_Pushover']['priority'])
pushover_retry = int(config['ttd_pre_notification_Pushover']['retry'])
pushover_expire = int(config['ttd_pre_notification_Pushover']['expire'])
pushover_sound = config['ttd_pre_notification_Pushover']['sound']

logging.info("Pushover settings loaded.")

# Access the Webhook and base audio URL
webhook_url = config['ttd_pre_notification_Webhook']['tone_detected_url']
base_audio_url = config['ttd_pre_notification_Webhook']['base_audio_url']
secondary_webhook_url = config['ttd_pre_notification_Webhook']['secondary_webhook_url']
timeout_seconds = int(config['ttd_pre_notification_Webhook']['timeout_seconds'])

logging.info("Webhook settings loaded.")

# Access the Retry logic settings
max_retries = int(config['ttd_pre_notification_Retry']['max_retries'])
initial_backoff = int(config['ttd_pre_notification_Retry']['initial_backoff'])
backoff_multiplier = int(config['ttd_pre_notification_Retry']['backoff_multiplier'])

logging.info("Retry logic settings loaded.")

# Access the File Handling settings
temp_directory = os.path.join(script_dir, config['ttd_pre_notification_FileHandling']['temp_directory'])
file_name_format = config['ttd_pre_notification_FileHandling']['file_name_format']

# Ensure the temp directory exists
if not os.path.exists(temp_directory):
    os.makedirs(temp_directory)

logging.info(f"Temporary files will be stored in: {temp_directory}")

# Access the Notification Content settings
title_prefix = config['ttd_pre_notification_NotificationContent']['title_prefix']
message_template = config['ttd_pre_notification_NotificationContent']['message_template']

logging.info("Notification content settings loaded.")

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
    now = datetime.now().timestamp()
    for filename in os.listdir(log_dir):
        file_path = os.path.join(log_dir, filename)
        if os.path.isfile(file_path) and now - os.path.getmtime(file_path) > max_log_days * 86400:
            os.remove(file_path)
            logging.info(f"Deleted old log file: {filename}")


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
            return True

        except requests.exceptions.ConnectionError as conn_err:
            logging.error(f"Attempt {attempt + 1}: Connection Error: {conn_err}")
            send_error_notification(f"Connection error encountered: {conn_err}")
            if attempt < retries - 1:
                logging.info("Retrying immediately due to connection error...")
                sleep(1)
            else:
                logging.error("Max retries reached for connection error.")
        
        except requests.exceptions.Timeout as timeout_err:
            logging.error(f"Attempt {attempt + 1}: Timeout Error: {timeout_err}")
            send_error_notification(f"Timeout error encountered: {timeout_err}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to timeout...")
                sleep(backoff_time)
                backoff_time *= backoff_multiplier
        
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"Attempt {attempt + 1}: HTTP Error: {http_err}")
            send_error_notification(f"HTTP error encountered: {http_err}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to HTTP error...")
                sleep(backoff_time)
                backoff_time *= backoff_multiplier
        
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Attempt {attempt + 1}: General Webhook Error: {req_err}")
            send_error_notification(f"General webhook error: {req_err}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to general error...")
                sleep(backoff_time)
                backoff_time *= backoff_multiplier

        attempt += 1

    send_error_notification(f"Webhook failed after {retries} attempts.")
    logging.error("Webhook failed after all retry attempts.")
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

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
def main():
    """
    Main function to parse arguments and initiate the webhook process.

    This function parses command-line arguments to extract the audio file name 
    and topic, then calls send_webhook() to send the information to Node-RED.
    """
    parser = argparse.ArgumentParser(description="Send a webhook to Node-RED with audio file details.")
    parser.add_argument('file_name', help="The name of the audio file.")
    parser.add_argument('topic', help="The topic for the webhook and notification.")
    parser.add_argument('--retries', type=int, default=max_retries, help="Number of retry attempts for sending the webhook.")
    
    args = parser.parse_args()

    logging.info(f"Received arguments: {args}")
    logging.info(f"Sending webhook for file: {args.file_name} with topic: {args.topic}")

    if not send_webhook(args.file_name, args.topic, args.retries):
        logging.error("Failed to send webhook after multiple attempts.")
    
    # Perform log cleanup after execution
    cleanup_logs()

if __name__ == "__main__":
    main()
