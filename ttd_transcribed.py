import os          # Provides functions for interacting with the operating system (e.g., file operations)
import sys         # Used for system-specific parameters and functions (e.g., command-line arguments)
import logging     # Used for logging messages for debugging or auditing purposes
import json        # Used for working with JSON data (e.g., reading and writing JSON files)
import argparse    # Used for parsing command-line arguments
from datetime import datetime, timedelta  # Provides classes for working with dates and times
from time import sleep  # Used to pause execution for a specified number of seconds

import asyncio         # Provides support for asynchronous programming
import aiohttp         # Asynchronous HTTP client for making non-blocking requests
import whisper         # OpenAI's Whisper model for transcribing audio
import configparser    # Used for reading configuration files (like `config.ini`)
import requests        # Makes HTTP requests to APIs or web services
from dotenv import load_dotenv  # Loads environment variables from a `.env` file
from tqdm import tqdm  # Used to create progress bars for long-running loops or operations
from ratelimit import limits, sleep_and_retry  # Implements rate limiting for API calls or requests


# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_transcribed.py
# Version: v1.7.0
# Author: Quentin King
# Creation Date: 09-07-2024
# Description:
# Transcribes audio files using Whisper AI and sends a webhook to Node-RED
# with enhanced error handling, logging, retry logic with exponential backoff,
# asynchronous requests, Pushover notifications, rate limiting, and persistent state recovery.
# -----------------------------------------------------------------------------
# Changelog:
# - v1.7.0 (09-07-2024): 
#   * Added separate logging levels for console and file logging.
#   * Improved command-line argument validation using argparse for better user experience.
#   * Implemented log cleanup strategies with time-based and count-based retention options.
#   * General code cleanup and optimization.
# - v1.6.0 (09-07-2024): 
#   * Added rate limiting for webhooks and Pushover notifications.
#   * Added a progress bar for transcriptions.
#   * Improved logging with exception-specific handling and more detailed logs.
# - v1.5.0 (09-07-2024): 
#   * Introduced asynchronous webhook requests for better performance.
#   * Added persistent state recovery to resume transcription after failures.
#   * Modularized the code for better readability and maintainability.
# - v1.4.0 (09-07-2024): 
#   * Added exponential backoff for retries in webhook sending.
#   * Corrected base_path configuration.
#   * Improved logging and error handling.
# -----------------------------------------------------------------------------
# Usage: python ttd_transcribed.py <mp3_file> <department> 
# Example: python ttd_transcribed.py audio.mp3 sales
# -----------------------------------------------------------------------------
# Environment Variables:
# - PUSHOVER_TOKEN: Pushover API token for sending notifications.
# - PUSHOVER_USER: Pushover user key for sending notifications.
# -----------------------------------------------------------------------------
# Dependencies: aiohttp, requests, ratelimit, tqdm, python-dotenv
# -----------------------------------------------------------------------------
# Whisper AI: https://whisper.ai/
# Pushover: https://pushover.net/
# -----------------------------------------------------------------------------
# License: MIT License
# -----------------------------------------------------------------------------
# Disclaimer: This script is provided as-is without any warranties. Use at your own risk.
# -----------------------------------------------------------------------------
# Credits: This script was created by Quentin King and inspired by the work of many others.
# -----------------------------------------------------------------------------
# References:
# - Whisper AI: https://whisper.ai/
# - Pushover: https://pushover.net/
# - Ratelimit: https://pypi.org/project/ratelimit/
# - Python-Dotenv: https://pypi.org/project/python-dotenv/
# - TQDM: https://pypi.org/project/tqdm/
# - AIOHTTP: https://docs.aiohttp.org/en/stable/
# -----------------------------------------------------------------------------
# Future Improvements:
# - Implement more advanced error handling and retry logic.
# - Add support for multiple audio files and batch processing.
# - Enhance the logging and error messages for better troubleshooting.
# - Implement more advanced rate limiting and backoff strategies.
# - Add support for additional AI models and transcription services.
# -----------------------------------------------------------------------------
# Feedback: If you have any suggestions or feedback, please let me know.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Load Environment Variables
# -----------------------------------------------------------------------------
load_dotenv()

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.ini')

# Load configuration from the config.ini file
config = configparser.ConfigParser()
config.read([config_path])

# Access logging configuration
log_dir = os.path.join(script_dir, config['ttd_transcribed_Logging']['log_dir'])
log_level = config['ttd_transcribed_Logging']['log_level']
console_log_level = config.get('ttd_transcribed_Logging', 'console_log_level', fallback='INFO')  # New setting for console log level
delete_after_process = config.getboolean('ttd_transcribed_FileHandling', 'delete_after_process', fallback=False)
log_to_console = config.getboolean('ttd_transcribed_Logging', 'log_to_console')


# Access Whisper configuration
model_size = config['ttd_transcribed_Whisper']['model_size']
temperature = float(config['ttd_transcribed_Whisper']['temperature'])
timestamps = config.getboolean('ttd_transcribed_Whisper', 'timestamps')
language = config.get('ttd_transcribed_Whisper', 'language', fallback=None)
beam_size = config.getint('ttd_transcribed_Whisper', 'beam_size')
best_of = config.getint('ttd_transcribed_Whisper', 'best_of')
no_speech_threshold = float(config['ttd_transcribed_Whisper']['no_speech_threshold'])
compression_ratio_threshold = float(config['ttd_transcribed_Whisper']['compression_ratio_threshold'])
logprob_threshold = float(config['ttd_transcribed_Whisper']['logprob_threshold'])
initial_prompt = config.get('ttd_transcribed_Whisper', 'initial_prompt', fallback=None)
condition_on_previous_text = config.getboolean('ttd_transcribed_Whisper', 'condition_on_previous_text', fallback=True)
verbose = config.getboolean('ttd_transcribed_Whisper', 'verbose', fallback=False)
task = config.get('ttd_transcribed_Whisper', 'task', fallback="transcribe")

# Access the Webhook and base audio URL
webhook_url = config['ttd_transcribed_Webhook']['ttd_transcribed_url']
base_audio_url = config['ttd_transcribed_Webhook']['base_audio_url']
timeout_seconds = int(config['ttd_transcribed_Webhook']['timeout_seconds'])
retry_limit = config.getint('ttd_transcribed_Retry', 'retry_limit', fallback=3)
retry_delay = config.getint('ttd_transcribed_Retry', 'retry_delay', fallback=5)

# Access Pushover settings with rate limiting
pushover_token = os.getenv('PUSHOVER_TOKEN')
pushover_user = os.getenv('PUSHOVER_USER')
pushover_priority = config['ttd_transcribed_Pushover']['priority']
pushover_rate_limit_seconds = config.getint('ttd_transcribed_Pushover', 'rate_limit_seconds', fallback=300)

# Define base path for audio files
base_path = config['ttd_transcribed_audio_Path']['base_path']

# Ensure the log directory and transcript directory exist
transcript_dir = os.path.join(log_dir, "transcripts")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
if not os.path.exists(transcript_dir):
    os.makedirs(transcript_dir)

# Configure logging
log_file_name = f"ttd_transcribed_{datetime.now().strftime('%m-%d-%Y_%H-%M-%S')}.log"
log_file_path = os.path.join(log_dir, log_file_name)


# File logging configuration
logging.basicConfig(
    filename=log_file_path,
    level=getattr(logging, log_level.upper(), logging.DEBUG),  # File logging level
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
)


# Console logging configuration
if log_to_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_log_level.upper(), logging.INFO))  # Console log level
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'))
    logging.getLogger().addHandler(console_handler)


logging.info("Logging initialized.")
logging.info(f"Logs will be stored in: {log_dir}")
logging.info(f"Log file: {log_file_name}")

# Persistent state file to resume transcription after an unexpected failure
persistent_state_path = os.path.join(script_dir, "state.json")

# Load log cleanup configuration
cleanup_enabled = config.getboolean('ttd_transcribed_LogCleanup', 'cleanup_enabled', fallback=False)
retention_strategy = config.get('ttd_transcribed_LogCleanup', 'retention_strategy', fallback='time')
retention_days = config.getint('ttd_transcribed_LogCleanup', 'retention_days', fallback=7)
max_log_files = config.getint('ttd_transcribed_LogCleanup', 'max_log_files', fallback=10)




# -----------------------------------------------------------------------------
# Function: cleanup_logs
# -----------------------------------------------------------------------------
def cleanup_logs():
    if not cleanup_enabled:
        logging.info("Log cleanup is disabled.")
        return

    log_files = sorted(
        [f for f in os.listdir(log_dir) if os.path.isfile(os.path.join(log_dir, f)) and f.startswith("ttd_transcribed_")],
        key=lambda f: os.path.getmtime(os.path.join(log_dir, f))
    )

    if retention_strategy == 'time':
        logging.info(f"Performing time-based log cleanup. Retaining logs for {retention_days} days.")
        now = datetime.now()
        cutoff_time = now - timedelta(days=retention_days)

        for log_file in log_files:
            log_file_path = os.path.join(log_dir, log_file)
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(log_file_path))

            if file_mod_time < cutoff_time:
                try:
                    os.remove(log_file_path)
                    logging.info(f"Deleted old log file: {log_file_path}")
                except Exception as e:
                    logging.error(f"Error deleting log file {log_file_path}: {e}")

    elif retention_strategy == 'count':
        logging.info(f"Performing count-based log cleanup. Retaining the latest {max_log_files} logs.")
        if len(log_files) > max_log_files:
            logs_to_delete = log_files[:-max_log_files]  # Oldest files to delete
            for log_file in logs_to_delete:
                log_file_path = os.path.join(log_dir, log_file)
                try:
                    os.remove(log_file_path)
                    logging.info(f"Deleted excess log file: {log_file_path}")
                except Exception as e:
                    logging.error(f"Error deleting log file {log_file_path}: {e}")

    else:
        logging.warning(f"Unknown retention strategy: {retention_strategy}")

# Call the log cleanup function at script startup
cleanup_logs()

# -----------------------------------------------------------------------------
# Function: load_persistent_state
# -----------------------------------------------------------------------------
def load_persistent_state():
    if os.path.exists(persistent_state_path):
        with open(persistent_state_path, 'r') as f:
            return json.load(f)
    return None

# -----------------------------------------------------------------------------
# Function: save_persistent_state
# -----------------------------------------------------------------------------
def save_persistent_state(state):
    with open(persistent_state_path, 'w') as f:
        json.dump(state, f)

# -----------------------------------------------------------------------------
# Function: transcribe_audio
# -----------------------------------------------------------------------------
def transcribe_audio(mp3_file):
    # Load the Whisper model based on the configuration
    logging.info(f"Loading Whisper model: {model_size} with temperature: {temperature}")
    model = whisper.load_model(model_size)

    # Transcribe the audio file with the configured settings
    logging.info(f"Starting transcription for {mp3_file}")
    result = model.transcribe(mp3_file, 
                              temperature=temperature,
                              language=language,
                              beam_size=beam_size,
                              best_of=best_of,
                              no_speech_threshold=no_speech_threshold,
                              compression_ratio_threshold=compression_ratio_threshold,
                              logprob_threshold=logprob_threshold,
                              initial_prompt=initial_prompt,
                              condition_on_previous_text=condition_on_previous_text,
                              verbose=verbose,
                              task=task)
    
    return result['text']

# -----------------------------------------------------------------------------
# Async Function: send_webhook
# -----------------------------------------------------------------------------
@sleep_and_retry
@limits(calls=1, period=pushover_rate_limit_seconds)  # Rate-limiting for webhook
async def send_webhook(mp3_file, department, transcription):
    file_name = os.path.basename(mp3_file)
    file_url = f"{base_audio_url}{file_name}"
    
    payload = {
        "msg": {
            "title": f"{department} Audio Transcribed",
            "payload": transcription,
            "url": file_url,
            "url_title": file_name
        }
    }

    attempt = 0
    while attempt < retry_limit:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload, timeout=timeout_seconds) as response:
                    response.raise_for_status()
                    logging.info(f"Webhook sent successfully for {file_name}.")
                    return True
        except aiohttp.ClientError as e:
            logging.error(f"Failed to send webhook for {file_name}: {e}")
            attempt += 1
            backoff_time = retry_delay * (2 ** (attempt - 1))  # Exponential backoff
            if attempt < retry_limit:
                logging.info(f"Retrying webhook in {backoff_time} seconds...")
                await asyncio.sleep(backoff_time)
    
    logging.error(f"Webhook failed after {retry_limit} attempts.")
    send_pushover_notification("Webhook Failure", f"Failed to send webhook for {file_name} after {retry_limit} attempts.")
    return False

# -----------------------------------------------------------------------------
# Function: send_pushover_notification
# -----------------------------------------------------------------------------
@sleep_and_retry
@limits(calls=1, period=pushover_rate_limit_seconds)  # Rate-limiting for Pushover notifications
def send_pushover_notification(title, message):
    payload = {
        "token": pushover_token,
        "user": pushover_user,
        "message": message,
        "title": title,
        "priority": pushover_priority,
    }

    try:
        response = requests.post("https://api.pushover.net/1/messages.json", data=payload)
        response.raise_for_status()
        logging.info(f"Pushover notification sent successfully: {title}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Pushover notification: {e}")

# -----------------------------------------------------------------------------
# Main Function: process_file
# -----------------------------------------------------------------------------
async def process_file(mp3_file, department):
    try:
        if not os.path.isfile(mp3_file):
            raise FileNotFoundError(f"MP3 file not found: {mp3_file}")

        # Transcribe the audio file
        transcription = transcribe_audio(mp3_file)
        logging.info(f"Transcription completed for {mp3_file}: {transcription}")

        # Save the transcription to a file
        transcript_file_path = os.path.join(transcript_dir, f"{os.path.basename(mp3_file)}.txt")
        with open(transcript_file_path, 'w') as f:
            f.write(transcription)
        logging.info(f"Transcription saved to: {transcript_file_path}")

        # Send the transcription via webhook
        if await send_webhook(mp3_file, department, transcription):
            if delete_after_process:
                os.remove(mp3_file)
                logging.info(f"Deleted processed file: {mp3_file}")

        # Clean up persistent state
        if os.path.exists(persistent_state_path):
            os.remove(persistent_state_path)

    except FileNotFoundError as fnf_error:
        logging.error(f"File not found: {fnf_error}")
    except aiohttp.ClientError as client_error:
        logging.error(f"Network error: {client_error}")
    except whisper.exceptions.WhisperException as whisper_error:
        logging.error(f"Whisper model error: {whisper_error}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        save_persistent_state({"mp3_file": mp3_file, "department": department})
        send_pushover_notification("Script Error", f"Unexpected error occurred: {e}")

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
async def main():
    logging.debug("Starting ttd_transcribed script.")
    
    # Use argparse to handle command-line arguments
    parser = argparse.ArgumentParser(description="Transcribe audio files and send the result via webhook.")
    parser.add_argument("mp3_file", type=str, help="The MP3 file to transcribe.")
    parser.add_argument("department", type=str, help="The department the file belongs to.")

    args = parser.parse_args()

    state = load_persistent_state()

    if state:
        mp3_file = state['mp3_file']
        department = state['department']
        logging.info(f"Resuming transcription for file: {mp3_file}, Department: {department}")
    else:
        mp3_file = os.path.join(base_path, args.mp3_file)
        department = args.department
        logging.info(f"Processing transcription for file: {mp3_file}, Department: {department}")

    # Now call the processing function
    await process_file(mp3_file, department)

if __name__ == "__main__":
    asyncio.run(main())