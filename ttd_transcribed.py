import os
import sys
import logging
import json
import argparse
from datetime import datetime, timedelta
from time import sleep, time
import asyncio
import aiohttp
import whisper
import configparser
import requests
import psutil
from prometheus_client import Counter
from dotenv import load_dotenv
from tqdm import tqdm
from ratelimit import limits, sleep_and_retry

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_transcribed.py
# Version: v1.8.2
# Author: Quentin King
# Creation Date: 09-07-2024
# Description:
# Transcribes audio files using Whisper AI and sends a webhook to Node-RED
# with enhanced error handling, logging, retry logic with exponential backoff,
# asynchronous requests, Pushover notifications, rate limiting, and persistent state recovery.
# -----------------------------------------------------------------------------
# Changelog:
# - v1.8.2 (09-07-2024): 
#   * Added logging for CPU and memory usage using `psutil`.
#   * Implemented time-based and count-based log cleanup.
#   * Added Prometheus custom metrics for success/failure counts (transcription and webhooks).
#   * Fixed persistent state path handling.
#   * Updated version control information.
# - v1.8.1 (09-07-2024): 
#   * Fixed missing imports and undefined variables in the cleanup_logs function.
#   * Added support for passing log directory, retention strategy, and other parameters to cleanup_logs.
# - v1.8.0 (09-07-2024): 
#   * Added performance monitoring (timing and resource usage) using time and psutil.
#   * Added custom metrics for transcription and webhook success/failure.
#   * Added cProfile profiling for better performance insights.
# -----------------------------------------------------------------------------
# Usage: python ttd_transcribed.py <mp3_file> <department> 
# Example: python ttd_transcribed.py audio.mp3 sales
# -----------------------------------------------------------------------------
# Environment Variables:
# - PUSHOVER_TOKEN: Pushover API token for sending notifications.
# - PUSHOVER_USER: Pushover user key for sending notifications.
# -----------------------------------------------------------------------------
# Dependencies: aiohttp, requests, ratelimit, tqdm, python-dotenv, psutil, prometheus_client
# -----------------------------------------------------------------------------
# Whisper AI: https://whisper.ai/
# Pushover: https://pushover.net/
# -----------------------------------------------------------------------------
# License: MIT License
# -----------------------------------------------------------------------------
# Disclaimer: This script is provided as-is without any warranties. Use at your own risk.
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
console_log_level = config.get('ttd_transcribed_Logging', 'console_log_level', fallback='INFO')
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
persistent_state_path = os.path.join(script_dir, 'persistent_state.json')
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
    level=getattr(logging, log_level.upper(), logging.DEBUG),
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
)

# Console logging configuration
if log_to_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_log_level.upper(), logging.INFO))
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'))
    logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized.")
logging.info(f"Logs will be stored in: {log_dir}")
logging.info(f"Log file: {log_file_name}")

# -----------------------------------------------------------------------------
# Performance Monitoring: Log CPU and memory usage
# -----------------------------------------------------------------------------
def log_system_usage():
    """Logs the current memory and CPU usage."""
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    cpu_usage = process.cpu_percent(interval=1)
    logging.info(f"Memory usage: {memory_info.rss / (1024 * 1024):.2f} MB, CPU usage: {cpu_usage:.2f}%")

# -----------------------------------------------------------------------------
# Function: cleanup_logs
# -----------------------------------------------------------------------------
def cleanup_logs(log_dir, cleanup_enabled, retention_strategy, retention_days=None, max_log_files=None):
    """Cleans up old logs based on retention strategy."""
    if not cleanup_enabled:
        logging.info("Log cleanup is disabled.")
        return

    log_files = sorted(
        [f for f in os.listdir(log_dir) if os.path.isfile(os.path.join(log_dir, f)) and f.startswith("ttd_transcribed_")],
        key=lambda f: os.path.getmtime(os.path.join(log_dir, f))  # Sort by modification time
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
cleanup_logs(
    log_dir=log_dir,
    cleanup_enabled=config.getboolean('ttd_transcribed_LogCleanup', 'cleanup_enabled', fallback=False),
    retention_strategy=config.get('ttd_transcribed_LogCleanup', 'retention_strategy', fallback='time'),
    retention_days=config.getint('ttd_transcribed_LogCleanup', 'retention_days', fallback=7),
    max_log_files=config.getint('ttd_transcribed_LogCleanup', 'max_log_files', fallback=10)
)

# -----------------------------------------------------------------------------
# Function: load_persistent_state
# -----------------------------------------------------------------------------
def load_persistent_state():
    """Loads the persistent state from a file."""
    if os.path.exists(persistent_state_path):
        with open(persistent_state_path, 'r') as f:
            return json.load(f)
    return None

# -----------------------------------------------------------------------------
# Function: save_persistent_state
# -----------------------------------------------------------------------------
def save_persistent_state(state):
    """Saves the current state to a file for recovery after an interruption."""
    with open(persistent_state_path, 'w') as f:
        json.dump(state, f)

# -----------------------------------------------------------------------------
# Custom Metrics: Transcription and Webhook Success/Failure
# -----------------------------------------------------------------------------
transcription_success = Counter('transcription_success_total', 'Total number of successful transcriptions')
transcription_failure = Counter('transcription_failure_total', 'Total number of failed transcriptions')
webhook_success = Counter('webhook_success_total', 'Total number of successful webhook requests')
webhook_failure = Counter('webhook_failure_total', 'Total number of failed webhook requests')

# -----------------------------------------------------------------------------
# Function: transcribe_audio
# -----------------------------------------------------------------------------
def transcribe_audio(mp3_file):
    """Transcribes an audio file using Whisper AI and logs performance."""
    start_time = time()  # Track start time for performance
    log_system_usage()  # Log CPU and memory usage before transcription

    logging.info(f"Loading Whisper model: {model_size} with temperature: {temperature}")
    model = whisper.load_model(model_size)

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
    
    end_time = time()  # Track end time
    logging.info(f"Transcription completed in {end_time - start_time:.2f} seconds for {mp3_file}")
    transcription_success.inc()  # Increment transcription success counter

    log_system_usage()  # Log CPU and memory usage after transcription

    return result['text']

# -----------------------------------------------------------------------------
# Async Function: send_webhook
# -----------------------------------------------------------------------------
@sleep_and_retry
@limits(calls=1, period=pushover_rate_limit_seconds)  # Rate-limiting for webhook
async def send_webhook(mp3_file, department, transcription):
    """Sends the transcription result via a webhook."""
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
                    webhook_success.inc()  # Increment webhook success counter
                    return True
        except aiohttp.ClientError as e:
            logging.error(f"Failed to send webhook for {file_name}: {e}")
            attempt += 1
            backoff_time = retry_delay * (2 ** (attempt - 1))  # Exponential backoff
            if attempt < retry_limit:
                logging.info(f"Retrying webhook in {backoff_time} seconds...")
                await asyncio.sleep(backoff_time)
    
    logging.error(f"Webhook failed after {retry_limit} attempts.")
    webhook_failure.inc()  # Increment webhook failure counter
    send_pushover_notification("Webhook Failure", f"Failed to send webhook for {file_name} after {retry_limit} attempts.")
    return False

# -----------------------------------------------------------------------------
# Function: send_pushover_notification
# -----------------------------------------------------------------------------
@sleep_and_retry
@limits(calls=1, period=pushover_rate_limit_seconds)  # Rate-limiting for Pushover notifications
def send_pushover_notification(title, message):
    """Sends a Pushover notification."""
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
    """Processes the MP3 file: transcribes and sends the result via a webhook."""
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
        transcription_failure.inc()  # Increment transcription failure counter
    except aiohttp.ClientError as client_error:
        logging.error(f"Network error: {client_error}")
        webhook_failure.inc()  # Increment webhook failure counter
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
