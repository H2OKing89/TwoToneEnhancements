import os
import sys
import logging
import requests
import configparser
from datetime import datetime
from time import sleep
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_transcribe.py
# Version: v1.0.0
# Author: Quentin King
# Creation Date: 09-06-2024
# Description: This script transcribes audio files and sends a webhook to Node-RED 
#              with the transcription details. It uses logging, a config file for 
#              configuration, and is modular for easy updates.
# -----------------------------------------------------------------------------
# Changelog:
# - v1.0.0 (09-06-2024): Initial creation of the script. Added basic functionality 
#                        for transcription, webhook integration, and logging.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Load Environment Variables (if needed)
# -----------------------------------------------------------------------------
load_dotenv()

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Get the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))

# Define paths to the config.ini file
config_path = os.path.join(script_dir, 'config.ini')

# Load configuration from the config.ini file
config = configparser.ConfigParser()
config.read([config_path])

# Access the Logging configuration
log_dir = os.path.join(script_dir, config['ttd_transcribe_Logging']['log_dir'])
log_level = config['ttd_transcribe_Logging']['log_level']
max_logs = int(config['ttd_transcribe_Logging']['max_logs'])
max_log_days = int(config['ttd_transcribe_Logging']['max_log_days'])
log_to_console = config.getboolean('ttd_transcribe_Logging', 'log_to_console')

# Access the Webhook and base audio URL
webhook_url = config['ttd_transcribe_Webhook']['ttd_transcribe_url']
base_audio_url = config['ttd_transcribe_Webhook']['base_audio_url']
timeout_seconds = int(config['ttd_transcribe_Webhook']['timeout_seconds'])

# Access the Base Path for Audio Files
base_path = config['ttd_transcribe_audio_Path']['base_path']

# Ensure the log directory exists
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logging
log_file_name = f"ttd_transcribe_{datetime.now().strftime('%m-%d-%Y_%H-%M-%S')}.log"
log_file_path = os.path.join(log_dir, log_file_name)

logging.basicConfig(
    filename=log_file_path,
    level=getattr(logging, log_level.upper(), logging.DEBUG),
    format='%(asctime)s - %(levelname)s - %(message)s'
)

if log_to_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized.")
logging.info(f"Logs will be stored in: {log_dir}")
logging.info(f"Log file: {log_file_name}")

# -----------------------------------------------------------------------------
# Function: send_webhook
# -----------------------------------------------------------------------------
def send_webhook(mp3_file, department):
    file_name = os.path.basename(mp3_file)
    file_url = f"{base_audio_url}{file_name}"
    
    payload = {
        "msg": {
            "title": f"{department} Audio Transcribed",
            "payload": f"{department} Audio has been processed.",
            "url": file_url,
            "url_title": file_name
        }
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        logging.info(f"Webhook sent successfully for {file_name}.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send webhook for {file_name}: {e}")
        raise

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
def main():
    logging.debug("Starting ttd_transcribe script.")
    
    try:
        if len(sys.argv) != 3:
            raise ValueError(f"Expected 3 arguments (script name, mp3 file, department), but got {len(sys.argv)}.")

        mp3_file = os.path.join(base_path, sys.argv[1])
        department = sys.argv[2]
        logging.info(f"Processing transcription for file: {mp3_file}, Department: {department}")

        if os.path.isfile(mp3_file):
            send_webhook(mp3_file, department)
        else:
            raise FileNotFoundError(f"MP3 file not found: {mp3_file}")
    
    except ValueError as ve:
        logging.error(f"ValueError encountered: {ve}")
    except FileNotFoundError as fnf_error:
        logging.error(f"FileNotFoundError encountered: {fnf_error}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        logging.info("ttd_transcribe script completed.")

if __name__ == "__main__":
    main()
