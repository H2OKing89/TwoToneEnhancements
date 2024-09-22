#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import json
import argparse
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import platform

import asyncio
import aiohttp
import aiofiles
import whisper
import psutil
import torch
from prometheus_client import Counter, start_http_server
from dotenv import load_dotenv
import signal

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_transcribed.py
# Version: v2.1.0
# Author: Quentin King
# Creation Date: 09-07-2023
# Last Updated: 09-16-2024
# Description:
# Transcribes audio files using Whisper AI, sends webhook to Node-RED, and includes
# log cleanup, persistent state, and Prometheus monitoring.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Load Environment Variables
# -----------------------------------------------------------------------------
load_dotenv()

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))  # Define script directory
config_path = os.path.join(script_dir, 'ttd_transcribed_config.json')  # Path to JSON configuration file

# Load configuration from JSON file
with open(config_path, 'r') as f:
    config = json.load(f)

# Access logging configuration
log_dir = os.path.join(script_dir, config['ttd_transcribed_Logging']['log_dir'])
log_level = config['ttd_transcribed_Logging'].get('log_level', 'DEBUG')  # Default to DEBUG
console_log_level = config['ttd_transcribed_Logging'].get('console_log_level', 'DEBUG')  # Default to DEBUG

delete_after_process = config['ttd_transcribed_Logging']['delete_after_processing']
log_to_console = config['ttd_transcribed_Logging']['log_to_console']

# Access Whisper configuration
model_size = config['ttd_transcribed_Whisper']['model_size']
temperature = config['ttd_transcribed_Whisper']['temperature']
timestamps = config['ttd_transcribed_Whisper']['timestamps']
language = config['ttd_transcribed_Whisper']['language']
beam_size = config['ttd_transcribed_Whisper']['beam_size']
best_of = config['ttd_transcribed_Whisper']['best_of']
no_speech_threshold = config['ttd_transcribed_Whisper']['no_speech_threshold']
compression_ratio_threshold = config['ttd_transcribed_Whisper']['compression_ratio_threshold']
logprob_threshold = config['ttd_transcribed_Whisper']['logprob_threshold']
condition_on_previous_text = config['ttd_transcribed_Whisper']['condition_on_previous_text']
verbose = config['ttd_transcribed_Whisper']['verbose']
task = config['ttd_transcribed_Whisper']['task']

# Webhook and audio URL configuration
webhook_url = config['ttd_transcribed_Webhook']['ttd_transcribed_url']
base_audio_url = config['ttd_transcribed_Webhook']['base_audio_url']
timeout_seconds = config['ttd_transcribed_Webhook']['timeout_seconds']
retry_limit = config['ttd_transcribed_Webhook'].get('retry_limit', 3)
retry_delay = config['ttd_transcribed_Webhook'].get('retry_delay', 5)

# Pushover notification settings
pushover_token = os.getenv('PUSHOVER_TOKEN')
pushover_user = os.getenv('PUSHOVER_USER')
pushover_priority = config['ttd_transcribed_Pushover']['priority']
pushover_rate_limit_seconds = config['ttd_transcribed_Pushover']['rate_limit_seconds']

# Audio file path
base_path = config['ttd_transcribed_audio_Path']['base_path']

# Ensure log and transcript directories exist
transcript_dir = os.path.join(log_dir, "transcripts")
persistent_state_path = os.path.join(script_dir, 'persistent_state.json')  # Path for persistent state
os.makedirs(log_dir, exist_ok=True)
os.makedirs(transcript_dir, exist_ok=True)

# Configure logging to both file and console
log_file_name = f"ttd_transcribed_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
log_file_path = os.path.join(log_dir, log_file_name)

# Create a logger
logger = logging.getLogger('ttd_transcribed')
logger.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))

# File handler
file_handler = logging.FileHandler(log_file_path)
file_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Console handler
if log_to_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_log_level.upper(), logging.INFO))
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

logger.info("Logging initialized.")
logger.info(f"Logs will be stored in: {log_dir}")
logger.info(f"Log file: {log_file_name}")

# -----------------------------------------------------------------------------
# Performance Monitoring: Log CPU and memory usage
# -----------------------------------------------------------------------------
def log_system_usage() -> None:
    """
    Logs the current memory and CPU usage of the script.
    """
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    cpu_usage = process.cpu_percent(interval=None)
    logger.info(f"Memory usage: {memory_info.rss / (1024 * 1024):.2f} MB, CPU usage: {cpu_usage:.2f}%")

# -----------------------------------------------------------------------------
# Log Cleanup Functionality
# -----------------------------------------------------------------------------
def cleanup_logs(log_dir: str, cleanup_enabled: bool, retention_strategy: str, retention_days: Optional[int] = None, max_log_files: Optional[int] = None) -> None:
    """
    Cleans up old log files based on time-based or count-based retention strategy.
    """
    if not cleanup_enabled:
        logger.info("Log cleanup is disabled.")
        return

    try:
        with os.scandir(log_dir) as entries:
            log_files = sorted(
                [entry for entry in entries if entry.is_file() and entry.name.startswith("ttd_transcribed_")],
                key=lambda e: e.stat().st_mtime
            )

            # Time-based log cleanup
            if retention_strategy == 'time' and retention_days is not None:
                now = datetime.now()
                cutoff_time = now - timedelta(days=retention_days)

                for entry in log_files:
                    file_mod_time = datetime.fromtimestamp(entry.stat().st_mtime)
                    if file_mod_time < cutoff_time:
                        try:
                            os.remove(entry.path)
                            logger.info(f"Deleted old log file: {entry.path}")
                        except Exception as e:
                            logger.error(f"Error deleting log file {entry.path}: {e}")

            # Count-based log cleanup
            elif retention_strategy == 'count' and max_log_files is not None:
                if len(log_files) > max_log_files:
                    logs_to_delete = log_files[:-max_log_files]
                    for entry in logs_to_delete:
                        try:
                            os.remove(entry.path)
                            logger.info(f"Deleted excess log file: {entry.path}")
                        except Exception as e:
                            logger.error(f"Error deleting log file {entry.path}: {e}")
            else:
                logger.warning(f"Unknown retention strategy: {retention_strategy}")
    except Exception as e:
        logger.error(f"Error during log cleanup: {e}")

cleanup_logs(
    log_dir=log_dir,
    cleanup_enabled=config['ttd_transcribed_LogCleanup']['cleanup_enabled'],
    retention_strategy=config['ttd_transcribed_LogCleanup']['retention_strategy'],
    retention_days=config['ttd_transcribed_LogCleanup'].get('retention_days', 7),
    max_log_files=config['ttd_transcribed_LogCleanup'].get('max_log_files', 10)
)

# -----------------------------------------------------------------------------
# Persistent State Handling
# -----------------------------------------------------------------------------
def load_persistent_state() -> Optional[Dict[str, Any]]:
    """
    Loads the persistent state from a file, if available.
    """
    if os.path.exists(persistent_state_path):
        try:
            with open(persistent_state_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load persistent state: {e}")
    return None

def save_persistent_state(state: Dict[str, Any]) -> None:
    """
    Saves the current state to a file to allow resuming after an interruption.
    """
    temp_path = persistent_state_path + '.tmp'
    try:
        with open(temp_path, 'w') as f:
            json.dump(state, f)
        os.replace(temp_path, persistent_state_path)
    except Exception as e:
        logger.error(f"Failed to save persistent state: {e}")

# -----------------------------------------------------------------------------
# Error Logging
# -----------------------------------------------------------------------------
def log_error(exception: Exception, message: str = "An error occurred") -> None:
    """
    Logs an error message with the exception details.

    Parameters:
    exception (Exception): The caught exception.
    message (str): The error message to log alongside the exception.
    """
    logger.error(f"{message}: {str(exception)}", exc_info=True)

# -----------------------------------------------------------------------------
# Prometheus Metrics
# -----------------------------------------------------------------------------
transcription_success = Counter('transcription_success_total', 'Total number of successful transcriptions')
transcription_failure = Counter('transcription_failure_total', 'Total number of failed transcriptions')
webhook_success = Counter('webhook_success_total', 'Total number of successful webhook requests')
webhook_failure = Counter('webhook_failure_total', 'Total number of failed webhook requests')

# Start Prometheus metrics server
start_http_server(8000)
logger.info("Prometheus metrics server started on port 8000.")

# -----------------------------------------------------------------------------
# Load Whisper Model Globally
# -----------------------------------------------------------------------------
logger.info(f"Loading Whisper model: {model_size}")
try:
    model = whisper.load_model(model_size)
    if torch.cuda.is_available():
        logger.info("CUDA is available. Using GPU for inference.")
    else:
        logger.info("CUDA is not available. Using CPU for inference.")
except Exception as e:
    logger.error(f"Failed to load Whisper model: {e}")
    sys.exit(1)

# -----------------------------------------------------------------------------
# Transcribe Audio Function with Detailed Whisper Logging
# -----------------------------------------------------------------------------
def transcribe_audio(mp3_file: str, department: str) -> str:
    try:
        initial_prompt = config['ttd_transcribed_Whisper']['initial_prompts'].get(department, "General emergency dispatch communication.")
        
        start_time = datetime.now()
        logger.debug(f"Using initial prompt: {initial_prompt}")
        log_system_usage()

        logger.debug(f"Starting transcription for {mp3_file} using the Whisper model: {model_size}")
        result = model.transcribe(
            mp3_file,
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
            task=task
        )

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        logger.debug(f"Transcription result: {result['text']}")
        logger.debug(f"Transcription logprobs: {result.get('logprobs', 'N/A')}")

        logger.info(f"Transcription completed in {duration:.2f} seconds for {mp3_file}")
        transcription_success.inc()
        log_system_usage()

        return result['text']
    except Exception as e:
        log_error(e, f"Failed to transcribe {mp3_file}")
        transcription_failure.inc()
        raise



# -----------------------------------------------------------------------------
# Async Function: send_webhook (Reverted to Previous Webhook Logic)
# -----------------------------------------------------------------------------
async def send_webhook(mp3_file: str, department: str, transcription: str, session: aiohttp.ClientSession) -> bool:
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

    logger.debug(f"Webhook payload: {json.dumps(payload, indent=2)}")

    attempt = 0
    backoff = retry_delay

    while attempt < retry_limit:
        try:
            logger.debug(f"Attempting to send webhook (attempt {attempt+1})")
            async with session.post(webhook_url, json=payload, timeout=timeout_seconds) as response:
                logger.debug(f"Webhook response status: {response.status}")
                response.raise_for_status()
                logger.info(f"Webhook sent successfully for {file_name}.")
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Failed to send webhook for {file_name}: {e}")
            attempt += 1
            if attempt < retry_limit:
                backoff_time = min(backoff * 2 ** (attempt - 1), 60)
                logger.info(f"Retrying webhook in {backoff_time} seconds...")
                await asyncio.sleep(backoff_time)
            else:
                logger.error(f"Webhook failed after {retry_limit} attempts.")
                break

    return False




# -----------------------------------------------------------------------------
# Async Function: send_pushover_notification_async
# -----------------------------------------------------------------------------
async def send_pushover_notification_async(title: str, message: str) -> None:
    if not pushover_token or not pushover_user:
        logger.warning("Pushover credentials not set. Cannot send notification.")
        return

    payload = {
        "token": pushover_token,
        "user": pushover_user,
        "message": message,
        "title": title,
        "priority": pushover_priority,
    }

    logger.debug(f"Sending Pushover notification with payload: {json.dumps(payload, indent=2)}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.pushover.net/1/messages.json", data=payload) as response:
                logger.debug(f"Pushover response status: {response.status}")
                response.raise_for_status()
                logger.info(f"Pushover notification sent successfully: {title}")
    except aiohttp.ClientError as e:
        log_error(e, "Failed to send Pushover notification")


# -----------------------------------------------------------------------------
# Async Function: process_file (Updated to use base_path)
# -----------------------------------------------------------------------------
async def process_file(mp3_file: str, department: str) -> None:
    """
    Processes the MP3 file, transcribes it, saves the result, and sends it via webhook.

    Parameters:
    mp3_file (str): Path to the MP3 file.
    department (str): The department name related to the transcription.
    """
    # Construct the full path to the MP3 file using base_path
    full_audio_path = os.path.join(base_path, mp3_file)
    unique_id = os.path.basename(mp3_file)

    try:
        # Check if the file exists before processing
        if not os.path.isfile(full_audio_path):
            raise FileNotFoundError(f"MP3 file not found: {full_audio_path}")

        # Transcribe the audio file
        transcription = transcribe_audio(full_audio_path)
        logger.info(f"Transcription completed for {unique_id}")

        # Save the transcription to a file
        transcript_file_path = os.path.join(transcript_dir, f"{unique_id}.txt")
        async with aiofiles.open(transcript_file_path, 'w') as f:
            await f.write(transcription)
        logger.info(f"Transcription saved to: {transcript_file_path}")

        # Send the transcription via webhook
        async with aiohttp.ClientSession() as session:
            success = await send_webhook(mp3_file, department, transcription, session)
            if success and delete_after_process:
                os.remove(full_audio_path)
                logger.info(f"Deleted processed file: {full_audio_path}")

        # Clean up persistent state
        if os.path.exists(persistent_state_path):
            os.remove(persistent_state_path)

    except Exception as e:
        log_error(e, f"Error processing file: {unique_id}")
        save_persistent_state({"mp3_file": mp3_file, "department": department})
        await send_pushover_notification_async("Script Error", f"Error processing {unique_id}: {e}")


# -----------------------------------------------------------------------------
# Graceful Shutdown Handling
# -----------------------------------------------------------------------------
def setup_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """
    Sets up signal handlers for graceful shutdown.

    Parameters:
    loop (asyncio.AbstractEventLoop): The event loop to attach handlers to.
    """
    signals = (signal.SIGINT, signal.SIGTERM)

    for s in signals:
        try:
            loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(loop, signal=s)))
        except NotImplementedError:
            # Signal handlers are not implemented on Windows for ProactorEventLoop
            pass

async def shutdown(loop: asyncio.AbstractEventLoop, signal: Optional[signal.Signals] = None) -> None:
    """
    Performs cleanup operations before shutting down.

    Parameters:
    loop (asyncio.AbstractEventLoop): The event loop to stop.
    signal (Optional[signal.Signals]): The signal that triggered the shutdown.
    """
    if signal:
        logger.info(f"Received exit signal {signal.name}...")

    logger.info("Closing async tasks...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)

    loop.stop()

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
async def main() -> None:
    """
    Main function that handles script execution: parsing arguments, processing files, etc.
    """
    logger.debug("Starting ttd_transcribed script.")

    # Use argparse to handle command-line arguments
    parser = argparse.ArgumentParser(description="Transcribe audio files and send the result via webhook.")
    parser.add_argument("mp3_file", type=str, help="The MP3 file to transcribe.")
    parser.add_argument("department", type=str, help="The department the file belongs to.")
    parser.add_argument("--log-level", type=str, help="Set the logging level (e.g., DEBUG, INFO).")
    parser.add_argument("--config", type=str, help="Path to the configuration file.", default=config_path)

    args = parser.parse_args()

    # Override log level if specified
    if args.log_level:
        logger.setLevel(getattr(logging, args.log_level.upper(), logging.DEBUG))
        for handler in logger.handlers:
            handler.setLevel(getattr(logging, args.log_level.upper(), logging.DEBUG))

    # Process the file with department name included
    await process_file(args.mp3_file, args.department)

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        setup_signal_handlers(loop)
        loop.run_until_complete(main())
    except Exception as e:
        log_error(e, "Unexpected error in main execution")
    finally:
        logger.info("Script terminated.")
