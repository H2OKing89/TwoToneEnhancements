# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: TwoToneDetect Pre-Notification
# Version: v1.8.0
# Author: Quentin King
# Date: 08-31-2024
# Description: This script sends a pre-notification webhook to Node-RED with 
#              the audio file URL and relevant details. Includes error handling, 
#              Pushover notifications for failures, and retry mechanisms with 
#              exponential backoff. Configuration settings are loaded from 
#              separate INI files (config.ini and pushover.ini).
# Changelog:
# - v1.8.0: Updated to support new logging configuration with max_logs and max_log_days.
#           Adjusted to use the correct ttd_pre_notification configuration from config.ini.
# - v1.7.1: Updated log file naming convention, removed "webhook" from the filename.
# - v1.7.0: Updated to read from separate config.ini and pushover.ini files.
#           Enhanced error handling and updated logging configuration.
# -----------------------------------------------------------------------------

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
# Configuration
# -----------------------------------------------------------------------------
# Load configuration from the INI files
config = configparser.ConfigParser()

# Assuming all INI files are in the same directory as this script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load config.ini and pushover.ini
config.read([
    os.path.join(script_dir, 'config.ini'),
    os.path.join(script_dir, 'pushover.ini')
])

# Access the Pushover credentials
pushover_app_token = config['Pushover']['PUSHOVER_TOKEN']
pushover_user_key = config['Pushover']['PUSHOVER_USER']

# Access the Webhook and base audio URL from config.ini
webhook_url = config['ttd_pre_notification_Webhook']['ttd_pre_notification_url']
base_audio_url = config['ttd_pre_notification_Webhook']['base_audio_url']

# Set up logging configuration
log_config_section = 'ttd_pre_notification_Logging'
log_dir = config.get(log_config_section, 'log_dir')
log_level = config.get(log_config_section, 'log_level', fallback='INFO').upper()
max_logs = int(config.get(log_config_section, 'max_logs', fallback=10))
max_log_days = int(config.get(log_config_section, 'max_log_days', fallback=10))

# Create subdirectory for logs if it doesn't exist
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Generate a unique log file name for each run
log_file = os.path.join(log_dir, f"ttd_pre_notification_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Configure logging
logging.basicConfig(
    handlers=[RotatingFileHandler(log_file, maxBytes=1048576, backupCount=max_logs)],
    level=getattr(logging, log_level),
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Function to clean up old logs
def cleanup_logs():
    now = datetime.now()
    for filename in os.listdir(log_dir):
        file_path = os.path.join(log_dir, filename)
        if os.path.isfile(file_path):
            file_age_days = (now - datetime.fromtimestamp(os.path.getmtime(file_path))).days
            if file_age_days > max_log_days:
                os.remove(file_path)
                logging.info(f"Deleted old log file: {filename}")

# Run cleanup
cleanup_logs()

# -----------------------------------------------------------------------------
# Function: send_webhook
# Description: Sends a webhook to Node-RED with retry mechanism and tailored 
#              exception handling. Uses exponential backoff for retries.
# -----------------------------------------------------------------------------
def send_webhook(file_name, topic, retries=3):
    """
    Sends a webhook to Node-RED with the audio file URL and relevant details.

    This function attempts to send a webhook with the specified audio file and 
    topic. If the request fails, it retries up to the specified number of times, 
    using exponential backoff.

    Args:
        file_name (str): The name of the audio file to be included in the webhook.
        topic (str): The topic for the webhook and notification.
        retries (int): Number of retry attempts for sending the webhook (default is 3).

    Returns:
        bool: True if the webhook was sent successfully, False otherwise.
    """
    file_name = os.path.basename(file_name)  # Extract the file name
    file_url = f"{base_audio_url}{file_name}"  # Construct the full URL
    
    payload = {
        "payload": {
            "message": file_url,
            "title": topic,
            "topic": topic
        }
    }

    attempt = 0
    backoff_time = 5  # Initial backoff time in seconds

    while attempt < retries:
        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()  # Raise an HTTPError for bad responses
            logging.info(f"Webhook sent successfully: {payload}")
            return True
        
        except requests.exceptions.ConnectionError as conn_err:
            logging.error(f"Attempt {attempt + 1}: Connection Error: {conn_err}")
            send_error_notification(f"Connection error encountered: {conn_err}")
            # Immediate retry without waiting for connection issues
            if attempt < retries - 1:
                logging.info("Retrying immediately due to connection error...")
                sleep(1)
            else:
                logging.error("Max retries reached for connection error.")
        
        except requests.exceptions.Timeout as timeout_err:
            logging.error(f"Attempt {attempt + 1}: Timeout Error: {timeout_err}")
            send_error_notification(f"Timeout error encountered: {timeout_err}")
            # Exponential backoff for timeouts
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to timeout...")
                sleep(backoff_time)
                backoff_time *= 2
        
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"Attempt {attempt + 1}: HTTP Error: {http_err}")
            send_error_notification(f"HTTP error encountered: {http_err}")
            # Retry with exponential backoff for HTTP errors
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to HTTP error...")
                sleep(backoff_time)
                backoff_time *= 2
        
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Attempt {attempt + 1}: General Webhook Error: {req_err}, "
                          f"Status Code: {response.status_code if response else 'N/A'}, "
                          f"Response Content: {response.content if response else 'N/A'}")
            send_error_notification(f"General webhook error: {req_err}")
            # Retry with exponential backoff for general errors
            if attempt < retries - 1:
                logging.info(f"Retrying in {backoff_time} seconds due to general error...")
                sleep(backoff_time)
                backoff_time *= 2
        
        attempt += 1

    send_error_notification(f"Webhook failed after {retries} attempts.")
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
        "priority": 2,  # Set priority to 2 for emergency
        "retry": 60,    # Retry interval in seconds
        "expire": 3600  # Expiration time in seconds (1 hour)
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
    parser.add_argument('--retries', type=int, default=3, help="Number of retry attempts for sending the webhook.")
    
    args = parser.parse_args()

    logging.info(f"Received arguments: {args}")
    logging.info(f"Sending webhook for file: {args.file_name} with topic: {args.topic}")

    if not send_webhook(args.file_name, args.topic, args.retries):
        logging.error("Failed to send webhook after multiple attempts.")

if __name__ == "__main__":
    main()
