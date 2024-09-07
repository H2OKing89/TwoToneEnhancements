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
import requests
import psutil
from prometheus_client import Counter
from dotenv import load_dotenv
from tqdm import tqdm
from ratelimit import limits, sleep_and_retry
from pydantic import BaseModel, Field, HttpUrl, DirectoryPath, ValidationError

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_transcribed.py
# Version: v1.9.0
# Author: Quentin King
# Creation Date: 09-07-2024
# Description:
# Transcribes audio files using Whisper AI and sends a webhook to Node-RED
# with enhanced error handling, logging, retry logic with exponential backoff,
# asynchronous requests, Pushover notifications, rate limiting, and persistent state recovery.
# -----------------------------------------------------------------------------
# Changelog:
# - v1.9.0 (09-07-2024): 
#   * Introduced modular functions for error handling and common operations.
#   * Enhanced error handling with specific error messages for network, file, and transcription issues.
#   * Switched to `pydantic` for configuration validation and structure.
#   * Added detailed docstrings for all functions for better documentation.
#   * Updated version control and logging improvements.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Load Environment Variables
# -----------------------------------------------------------------------------
load_dotenv()

# -----------------------------------------------------------------------------
# Configuration Using pydantic for Validation
# -----------------------------------------------------------------------------
class Config(BaseModel):
    log_dir: DirectoryPath
    log_level: str
    pushover_token: str
    pushover_user: str
    webhook_url: HttpUrl
    base_audio_url: HttpUrl
    retry_limit: int = Field(..., gt=0)  # Must be greater than 0
    timeout_seconds: int = Field(..., gt=0)
    retention_days: int = Field(..., gt=0)
    max_log_files: int = Field(..., gt=0)
    cleanup_enabled: bool = True
    retention_strategy: str = Field(..., regex="^(time|count)$")

# Try loading the config
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    config_parser = configparser.ConfigParser()
    config_parser.read([config_path])

    config = Config(
        log_dir=config_parser.get('ttd_transcribed_Logging', 'log_dir'),
        log_level=config_parser.get('ttd_transcribed_Logging', 'log_level'),
        pushover_token=os.getenv('PUSHOVER_TOKEN'),
        pushover_user=os.getenv('PUSHOVER_USER'),
        webhook_url=config_parser.get('ttd_transcribed_Webhook', 'ttd_transcribed_url'),
        base_audio_url=config_parser.get('ttd_transcribed_Webhook', 'base_audio_url'),
        retry_limit=config_parser.getint('ttd_transcribed_Retry', 'retry_limit', fallback=3),
        timeout_seconds=config_parser.getint('ttd_transcribed_Webhook', 'timeout_seconds'),
        retention_days=config_parser.getint('ttd_transcribed_LogCleanup', 'retention_days', fallback=7),
        max_log_files=config_parser.getint('ttd_transcribed_LogCleanup', 'max_log_files', fallback=10),
        cleanup_enabled=config_parser.getboolean('ttd_transcribed_LogCleanup', 'cleanup_enabled', fallback=False),
        retention_strategy=config_parser.get('ttd_transcribed_LogCleanup', 'retention_strategy', fallback='time')
    )
except ValidationError as e:
    print(f"Configuration error: {e}")
    sys.exit(1)

# Ensure log directories exist
if not os.path.exists(config.log_dir):
    os.makedirs(config.log_dir)

# -----------------------------------------------------------------------------
# Logging Setup
# -----------------------------------------------------------------------------
log_file_name = f"ttd_transcribed_{datetime.now().strftime('%m-%d-%Y_%H-%M-%S')}.log"
log_file_path = os.path.join(config.log_dir, log_file_name)

logging.basicConfig(
    filename=log_file_path,
    level=getattr(logging, config.log_level.upper(), logging.DEBUG),
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
)

# -----------------------------------------------------------------------------
# Function: log_error
# -----------------------------------------------------------------------------
def log_error(error_type: str, message: str):
    """
    Logs a specific type of error with a detailed message.
    
    Parameters:
    error_type (str): The type of error (e.g., 'Network', 'File', 'Whisper').
    message (str): The detailed error message to log.
    """
    logging.error(f"{error_type} Error: {message}")

# -----------------------------------------------------------------------------
# Performance Monitoring: Log CPU and Memory Usage
# -----------------------------------------------------------------------------
def log_system_usage():
    """
    Logs the current memory and CPU usage of the script using psutil.
    """
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    cpu_usage = process.cpu_percent(interval=1)
    logging.info(f"Memory usage: {memory_info.rss / (1024 * 1024):.2f} MB, CPU usage: {cpu_usage:.2f}%")

# -----------------------------------------------------------------------------
# Function: cleanup_logs
# -----------------------------------------------------------------------------
def cleanup_logs(log_dir: str, cleanup_enabled: bool, retention_strategy: str, retention_days: int = None, max_log_files: int = None):
    """
    Cleans up old log files based on time-based or count-based retention strategy.
    
    Parameters:
    log_dir (str): Directory where the log files are stored.
    cleanup_enabled (bool): Whether log cleanup is enabled.
    retention_strategy (str): Strategy to use for log cleanup ('time' or 'count').
    retention_days (int, optional): Retain logs for this many days if using time-based cleanup.
    max_log_files (int, optional): Retain this many logs if using count-based cleanup.
    """
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
            logs_to_delete = log_files[:-max_log_files]
            for log_file in logs_to_delete:
                log_file_path = os.path.join(log_dir, log_file)
                try:
                    os.remove(log_file_path)
                    logging.info(f"Deleted excess log file: {log_file_path}")
                except Exception as e:
                    logging.error(f"Error deleting log file {log_file_path}: {e}")

    else:
        logging.warning(f"Unknown retention strategy: {retention_strategy}")

# Call cleanup at startup
cleanup_logs(
    log_dir=config.log_dir,
    cleanup_enabled=config.cleanup_enabled,
    retention_strategy=config.retention_strategy,
    retention_days=config.retention_days,
    max_log_files=config.max_log_files
)

# -----------------------------------------------------------------------------
# Function: transcribe_audio
# -----------------------------------------------------------------------------
def transcribe_audio(mp3_file: str) -> str:
    """
    Transcribes an audio file using Whisper AI and logs performance metrics.

    Parameters:
    mp3_file (str): Path to the MP3 file to be transcribed.

    Returns:
    str: The transcription result.
    """
    start_time = time()
    log_system_usage()

    logging.info(f"Loading Whisper model: {config.model_size} with temperature: {config.temperature}")
    model = whisper.load_model(config.model_size)

    logging.info(f"Starting transcription for {mp3_file}")
    result = model.transcribe(mp3_file, 
                              temperature=config.temperature,
                              language=config.language,
                              beam_size=config.beam_size,
                              best_of=config.best_of,
                              no_speech_threshold=config.no_speech_threshold,
                              compression_ratio_threshold=config.compression_ratio_threshold,
                              logprob_threshold=config.logprob_threshold,
                              initial_prompt=config.initial_prompt,
                              condition_on_previous_text=config.condition_on_previous_text,
                              verbose=config.verbose,
                              task=config.task)

    end_time = time()
    logging.info(f"Transcription completed in {end_time - start_time:.2f} seconds for {mp3_file}")
    return result['text']

# -----------------------------------------------------------------------------
# Async Function: send_webhook
# -----------------------------------------------------------------------------
@sleep_and_retry
@limits(calls=1, period=config.pushover_rate_limit_seconds)
async def send_webhook(mp3_file: str, department: str, transcription: str) -> bool:
    """
    Sends the transcription result via a webhook to the Node-RED server.

    Parameters:
    mp3_file (str): Path to the MP3 file.
    department (str): The department the file belongs to.
    transcription (str): The transcription text.

    Returns:
    bool: True if successful, False otherwise.
    """
    file_name = os.path.basename(mp3_file)
    file_url = f"{config.base_audio_url}{file_name}"
    
    payload = {
        "msg": {
            "title": f"{department} Audio Transcribed",
            "payload": transcription,
            "url": file_url,
            "url_title": file_name
        }
    }

    attempt = 0
    while attempt < config.retry_limit:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(config.webhook_url, json=payload, timeout=config.timeout_seconds) as response:
                    response.raise_for_status()
                    logging.info(f"Webhook sent successfully for {file_name}.")
                    return True
        except aiohttp.ClientError as e:
            log_error('Network', f"Failed to send webhook for {file_name}: {e}")
            attempt += 1
            backoff_time = config.retry_delay * (2 ** (attempt - 1))
            if attempt < config.retry_limit:
                logging.info(f"Retrying webhook in {backoff_time} seconds...")
                await asyncio.sleep(backoff_time)
    
    logging.error(f"Webhook failed after {config.retry_limit} attempts.")
    return False

# -----------------------------------------------------------------------------
# Function: send_pushover_notification
# -----------------------------------------------------------------------------
@sleep_and_retry
@limits(calls=1, period=config.pushover_rate_limit_seconds)
def send_pushover_notification(title: str, message: str):
    """
    Sends a Pushover notification in case of critical errors.

    Parameters:
    title (str): The notification title.
    message (str): The notification message.
    """
    payload = {
        "token": config.pushover_token,
        "user": config.pushover_user,
        "message": message,
        "title": title,
        "priority": config.pushover_priority,
    }

    try:
        response = requests.post("https://api.pushover.net/1/messages.json", data=payload)
        response.raise_for_status()
        logging.info(f"Pushover notification sent successfully: {title}")
    except requests.exceptions.RequestException as e:
        log_error('Pushover', f"Failed to send Pushover notification: {e}")

# -----------------------------------------------------------------------------
# Main Function: process_file
# -----------------------------------------------------------------------------
async def process_file(mp3_file: str, department: str):
    """
    Processes the MP3 file, transcribes it, saves the result, and sends it via webhook.

    Parameters:
    mp3_file (str): Path to the MP3 file.
    department (str): The department name related to the transcription.
    """
    try:
        if not os.path.isfile(mp3_file):
            raise FileNotFoundError(f"MP3 file not found: {mp3_file}")

        # Transcribe the audio file
        transcription = transcribe_audio(mp3_file)
        logging.info(f"Transcription completed for {mp3_file}: {transcription}")

        # Send the transcription via webhook
        if await send_webhook(mp3_file, department, transcription):
            if config.delete_after_process:
                os.remove(mp3_file)
                logging.info(f"Deleted processed file: {mp3_file}")

    except FileNotFoundError as fnf_error:
        log_error('File', str(fnf_error))
    except aiohttp.ClientError as client_error:
        log_error('Network', str(client_error))
    except whisper.exceptions.WhisperException as whisper_error:
        log_error('Whisper', str(whisper_error))
    except Exception as e:
        log_error('General', str(e))
        save_persistent_state({"mp3_file": mp3_file, "department": department})

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
async def main():
    """
    Main entry point for script execution. Handles argument parsing and processing.
    """
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
        mp3_file = os.path.join(config.base_path, args.mp3_file)
        department = args.department
        logging.info(f"Processing transcription for file: {mp3_file}, Department: {department}")

    await process_file(mp3_file, department)

if __name__ == "__main__":
    asyncio.run(main())
