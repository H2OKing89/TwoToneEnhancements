#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script Name: ttd_transcribed.py
Version: v3.0.5
Author: Quentin King
Creation Date: 09-07-2023
Last Updated: 10-05-2024
Description:
Transcribes audio files using Whisper AI, sends webhook to Node-RED, and includes
log cleanup, persistent state, and comprehensive logging with Pushover notifications.

Changelog:
v3.0.5 - 10-05-2024
- Added GPU usage measurement using pynvml.
- Fixed duplicate "Transcription Task Summary" headers in Pushover notifications.
- Enhanced logging to include GPU metrics.
- Updated dependencies to include pynvml.

v3.0.4 - 10-05-2024
- Fixed CPU usage measurement to accurately reflect process CPU usage by increasing interval.
- Removed Prometheus metrics server as it's no longer needed.
- Fixed duplicate "Transcription Task Summary" headers in Pushover notifications.
- Enhanced Pushover notifications to include accurate CPU usage information.

v3.0.3 - 10-05-2024
- Removed Prometheus metrics server as it's no longer needed.
- Fixed CPU usage measurement to accurately reflect process CPU usage.
- Enhanced Pushover notifications to include accurate CPU usage information.
- Ensured consistency of unique identifiers across all logging and notification functions.

v3.0.2 - 10-05-2024
- Changed transcript filenames to use original MP3 filenames without the '.mp3' extension.
- Updated logging configuration to correctly map log record attributes, eliminating KeyError: 'name'.
- Enhanced Pushover notifications to include CPU usage information.
- Ensured consistency of unique identifiers across all logging and notification functions.

v3.0.1 - 10-05-2024
- Fixed issue with transcripts being saved with UUID filenames instead of original filenames.
- Resolved logging errors (KeyError: 'name') by correcting the logging configuration.
- Updated logging formatter to ensure consistency between log record attributes and format string.

v3.0.0 - 10-05-2024
- Implemented structured logging in JSON format.
- Added unique identifiers for each transcription task.
- Enhanced contextual information in logs.
- Enabled complete stack tracing for errors.
- Categorized errors for better prioritization.
- Optimized log management with rotation and archiving.
- Standardized timestamps to ISO 8601 format.
- Ensured log level consistency across the script.
- Added detailed performance metric logging.
- Improved human-readable logs for critical events.
- Implemented anomaly detection alerts.
- Increased granularity in error logging.
"""

import os
import sys
import logging
import logging.config
import json
import argparse
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List
import asyncio
import aiohttp
import aiofiles
import whisper
import psutil
import torch
from dotenv import load_dotenv
import signal
from pythonjsonlogger import jsonlogger

# Import pynvml for GPU monitoring
try:
    import pynvml
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

# -----------------------------------------------------------------------------
# Load Environment Variables
# -----------------------------------------------------------------------------
load_dotenv()

# -----------------------------------------------------------------------------
# Configuration Paths
# -----------------------------------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'ttd_transcribed_config.json')

# -----------------------------------------------------------------------------
# Load Configuration
# -----------------------------------------------------------------------------
with open(config_path, 'r') as f:
    config = json.load(f)

# -----------------------------------------------------------------------------
# Validate Configuration
# -----------------------------------------------------------------------------
def validate_config(config: Dict[str, Any]) -> None:
    required_sections = [
        'ttd_transcribed_Pushover',
        'ttd_transcribed_Logging',
        'ttd_transcribed_LogCleanup',
        'ttd_transcribed_audio_Path',
        'ttd_transcribed_Webhook',
        'ttd_transcribed_Whisper'
    ]
    for section in required_sections:
        if section not in config:
            raise ValueError(f"Missing configuration section: {section}")

validate_config(config)

# -----------------------------------------------------------------------------
# Initialize Notification Lists
# -----------------------------------------------------------------------------
task_notifications: List[str] = []
error_notifications: List[str] = []

# -----------------------------------------------------------------------------
# Logging Configuration
# -----------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    """
    Sets up the logging configuration with JSON formatting and log rotation.
    """
    log_dir = os.path.join(script_dir, config['ttd_transcribed_Logging']['log_dir'])
    log_level = config['ttd_transcribed_Logging']['log_level']
    log_to_console = config['ttd_transcribed_Logging']['log_to_console']
    console_log_level = config['ttd_transcribed_Logging']['console_log_level']
    
    os.makedirs(log_dir, exist_ok=True)

    log_file_path = os.path.join(log_dir, 'ttd_transcribed.log')

    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'json': {
                '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
                'format': '%(asctime)s %(levelname)s %(name)s %(funcName)s %(lineno)d %(message)s',
                'rename_fields': {
                    'asctime': 'timestamp',
                    'name': 'logger',
                    'levelname': 'level',
                    'funcName': 'function',
                    'lineno': 'line_no',
                },
                'json_indent': 4
            },
            'standard': {
                'format': '%(asctime)s - %(levelname)s - %(name)s - %(funcName)s - %(lineno)d - %(message)s'
            },
        },
        'handlers': {
            'rotating_file': {
                'level': log_level.upper(),
                'class': 'logging.handlers.TimedRotatingFileHandler',
                'filename': log_file_path,
                'when': 'midnight',
                'interval': 1,
                'backupCount': 7,
                'formatter': 'json',
                'encoding': 'utf-8',
            },
            'console': {
                'level': console_log_level.upper(),
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
            },
        },
        'loggers': {
            'ttd_transcribed': {
                'handlers': ['rotating_file', 'console'] if log_to_console else ['rotating_file'],
                'level': log_level.upper(),
                'propagate': False,
            },
        },
    }

    logging.config.dictConfig(logging_config)
    logger = logging.getLogger('ttd_transcribed')
    logger.info("Logging initialized.", extra={'unique_id': 'N/A'})
    logger.info(f"Logs will be stored in: {log_dir}", extra={'unique_id': 'N/A'})
    logger.info(f"Log file: {log_file_path}", extra={'unique_id': 'N/A'})
    return logger

logger = setup_logging()

# -----------------------------------------------------------------------------
# Initialize pynvml for GPU Monitoring
# -----------------------------------------------------------------------------
if GPU_AVAILABLE:
    try:
        pynvml.nvmlInit()
        logger.info("NVIDIA Management Library (pynvml) initialized for GPU monitoring.", extra={'unique_id': 'N/A'})
    except pynvml.NVMLError as e:
        logger.error(f"Failed to initialize pynvml for GPU monitoring: {e}", extra={'unique_id': 'N/A'})
        GPU_AVAILABLE = False
else:
    logger.warning("pynvml is not installed. GPU usage will not be monitored.", extra={'unique_id': 'N/A'})

# -----------------------------------------------------------------------------
# Define log_task and log_error Functions
# -----------------------------------------------------------------------------
def log_task(message: str, unique_id: str) -> None:
    """
    Logs a task-related message with a unique identifier.
    
    Args:
        message (str): The task message to log.
        unique_id (str): The unique identifier for the task.
    """
    task_notifications.append(message)
    logger.info(message, extra={'unique_id': unique_id})

def log_error(message: str, unique_id: str) -> None:
    """
    Logs an error-related message with a unique identifier.
    
    Args:
        message (str): The error message to log.
        unique_id (str): The unique identifier for the task.
    """
    error_notifications.append(message)
    logger.error(message, extra={'unique_id': unique_id})

# -----------------------------------------------------------------------------
# Load Whisper Model with GPU Utilization
# -----------------------------------------------------------------------------
def load_whisper_model() -> whisper.Whisper:
    """
    Loads the Whisper model, utilizing GPU if available.
    """
    model_size = config['ttd_transcribed_Whisper']['model_size']
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading Whisper model '{model_size}' on device '{device}'.", extra={'unique_id': 'N/A'})
    model = whisper.load_model(model_size, device=device)
    return model

model = load_whisper_model()

# -----------------------------------------------------------------------------
# Log Cleanup Functionality
# -----------------------------------------------------------------------------
def cleanup_logs() -> None:
    """
    Cleans up old log files based on time-based or count-based retention strategy.
    """
    log_dir = os.path.join(script_dir, config['ttd_transcribed_Logging']['log_dir'])
    cleanup_enabled = config['ttd_transcribed_LogCleanup']['cleanup_enabled']
    retention_strategy = config['ttd_transcribed_LogCleanup']['retention_strategy']
    retention_days = config['ttd_transcribed_LogCleanup'].get('retention_days', 7)
    max_log_files = config['ttd_transcribed_LogCleanup'].get('max_log_files', 10)

    if not cleanup_enabled:
        logger.info("Log cleanup is disabled.", extra={'unique_id': 'N/A'})
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
                            log_task(f"Deleted old log file: {entry.path}", unique_id='N/A')
                        except Exception as e:
                            log_error(f"Error deleting log file {entry.path}: {e}", unique_id='N/A')

            # Count-based log cleanup
            elif retention_strategy == 'count' and max_log_files is not None:
                if len(log_files) > max_log_files:
                    logs_to_delete = log_files[:-max_log_files]
                    for entry in logs_to_delete:
                        try:
                            os.remove(entry.path)
                            log_task(f"Deleted excess log file: {entry.path}", unique_id='N/A')
                        except Exception as e:
                            log_error(f"Error deleting log file {entry.path}: {e}", unique_id='N/A')
            else:
                log_error(f"Unknown retention strategy: {retention_strategy}", unique_id='N/A')
    except Exception as e:
        log_error(f"Error during log cleanup: {e}", unique_id='N/A')

cleanup_logs()

# -----------------------------------------------------------------------------
# Performance Monitoring: Log CPU, GPU, and Memory Usage
# -----------------------------------------------------------------------------
# Initialize CPU percent measurement
psutil.cpu_percent(interval=None)

def log_system_usage(unique_id: str) -> None:
    """
    Logs the current memory, CPU, and GPU usage of the script.
    
    Args:
        unique_id (str): The unique identifier for the task.
    """
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    cpu_usage = process.cpu_percent(interval=1.5)  # Increased interval for accurate measurement

    usage_message = f"Memory usage: {memory_info.rss / (1024 * 1024):.2f} MB, CPU usage: {cpu_usage:.2f}%"

    if GPU_AVAILABLE:
        try:
            # Assuming single GPU; modify if multiple GPUs are used
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            gpu_util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
            gpu_memory = pynvml.nvmlDeviceGetMemoryInfo(handle).used / (1024 * 1024)  # in MB
            usage_message += f", GPU usage: {gpu_util}% | GPU Memory Usage: {gpu_memory:.2f} MB"
        except pynvml.NVMLError as e:
            usage_message += f", GPU usage: Error retrieving GPU metrics ({e})"

    log_task(usage_message, unique_id)

# -----------------------------------------------------------------------------
# Signal Handlers for Graceful Shutdown
# -----------------------------------------------------------------------------
def shutdown_handler(signum, frame):
    """
    Handles shutdown signals to allow graceful shutdown.
    """
    logger.info(f"Received shutdown signal ({signum}). Shutting down gracefully...", extra={'unique_id': 'N/A'})
    if GPU_AVAILABLE:
        try:
            pynvml.nvmlShutdown()
            logger.info("pynvml shutdown successfully.", extra={'unique_id': 'N/A'})
        except pynvml.NVMLError as e:
            logger.error(f"Error shutting down pynvml: {e}", extra={'unique_id': 'N/A'})
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# -----------------------------------------------------------------------------
# Function: send_pushover_notification_async
# -----------------------------------------------------------------------------
async def send_pushover_notification_async(
    title: str,
    message: str,
    priority: int = 0,
    sound: Optional[str] = None
) -> None:
    """
    Sends a Pushover notification for critical alerts asynchronously.
    
    Args:
        title (str): The title of the notification.
        message (str): The message content.
        priority (int, optional): The priority level of the notification.
        sound (str, optional): The sound to play with the notification.
    """
    pushover_token = os.getenv('PUSHOVER_TOKEN')
    pushover_user = os.getenv('PUSHOVER_USER')
    pushover_priority = config['ttd_transcribed_Pushover'].get('priority', 0)
    pushover_sound = config['ttd_transcribed_Pushover'].get('sound', 'pushover')

    if not pushover_token or not pushover_user:
        logger.warning("Pushover credentials not set. Cannot send notification.", extra={'unique_id': 'N/A'})
        return

    payload = {
        "token": pushover_token,
        "user": pushover_user,
        "message": message,
        "title": title,
        "priority": priority if priority else pushover_priority,
        "sound": sound if sound else pushover_sound
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.pushover.net/1/messages.json", data=payload) as response:
                response.raise_for_status()
                logger.info(f"Pushover notification sent successfully: {title}", extra={'unique_id': 'N/A'})
    except aiohttp.ClientError as e:
        log_error(f"Failed to send Pushover notification: {e}", unique_id='N/A')

# -----------------------------------------------------------------------------
# Retry Logic and Webhook Sending with Exponential Backoff
# -----------------------------------------------------------------------------
async def send_webhook(
    mp3_file: str,
    department: str,
    transcription: str,
    session: aiohttp.ClientSession,
    unique_id: str
) -> bool:
    """
    Sends the transcription result via a webhook with exponential backoff.
    
    Args:
        mp3_file (str): The name of the MP3 file.
        department (str): The department associated with the audio.
        transcription (str): The transcribed text.
        session (aiohttp.ClientSession): The aiohttp session to use for sending the request.
        unique_id (str): The unique identifier for the task.
    
    Returns:
        bool: True if the webhook was sent successfully, False otherwise.
    """
    webhook_url = config['ttd_transcribed_Webhook']['ttd_transcribed_url']
    base_audio_url = config['ttd_transcribed_Webhook']['base_audio_url']
    timeout_seconds = config['ttd_transcribed_Webhook'].get('timeout_seconds', 10)
    retry_limit = config['ttd_transcribed_Webhook'].get('retry_limit', 3)
    initial_retry_delay = config['ttd_transcribed_Webhook'].get('retry_delay', 5)

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
    backoff = initial_retry_delay

    while attempt < retry_limit:
        try:
            async with session.post(webhook_url, json=payload, timeout=timeout_seconds) as response:
                response.raise_for_status()
                logger.info(f"Webhook sent successfully for {file_name}.", extra={'unique_id': unique_id})
                log_task(f"Webhook sent successfully for {file_name}.", unique_id=unique_id)
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Failed to send webhook for {file_name}: {e}", exc_info=True, extra={'unique_id': unique_id})
            attempt += 1
            if attempt < retry_limit:
                backoff_time = min(backoff * 2 ** (attempt - 1), 60)
                logger.info(f"Retrying webhook in {backoff_time} seconds...", extra={'unique_id': unique_id})
                await asyncio.sleep(backoff_time)
            else:
                break

    log_error(f"Webhook failed after {retry_limit} attempts for {file_name}.", unique_id=unique_id)
    await send_pushover_notification_async(
        title="Webhook Failed",
        message=f"Failed to send webhook for {file_name} after {retry_limit} attempts.",
        priority=1
    )
    logger.error(f"Webhook failed after {retry_limit} attempts.", extra={'unique_id': unique_id})
    return False

# -----------------------------------------------------------------------------
# Function: transcribe_audio
# -----------------------------------------------------------------------------
def transcribe_audio(mp3_file: str, department: str, unique_id: str) -> Dict[str, Any]:
    """
    Transcribes an audio file using Whisper AI and logs performance metrics.
    
    Args:
        mp3_file (str): The path to the MP3 file to transcribe.
        department (str): The department associated with the audio.
        unique_id (str): The unique identifier for the task.
    
    Returns:
        Dict[str, Any]: A dictionary containing the transcription text and duration.
    """
    initial_prompt = config['ttd_transcribed_Whisper']['initial_prompts'].get(
        department, "General emergency dispatch communication."
    )
    duration: float = 0.0

    try:
        start_time = datetime.now()
        log_system_usage(unique_id)

        logger.info(f"Starting transcription for {mp3_file}", extra={'unique_id': unique_id})
        result = model.transcribe(
            mp3_file,
            temperature=config['ttd_transcribed_Whisper']['temperature'],
            language=config['ttd_transcribed_Whisper']['language'],
            beam_size=config['ttd_transcribed_Whisper']['beam_size'],
            best_of=config['ttd_transcribed_Whisper']['best_of'],
            no_speech_threshold=config['ttd_transcribed_Whisper']['no_speech_threshold'],
            compression_ratio_threshold=config['ttd_transcribed_Whisper']['compression_ratio_threshold'],
            logprob_threshold=config['ttd_transcribed_Whisper']['logprob_threshold'],
            initial_prompt=initial_prompt,
            condition_on_previous_text=config['ttd_transcribed_Whisper']['condition_on_previous_text'],
            verbose=config['ttd_transcribed_Whisper']['verbose'],
            task=config['ttd_transcribed_Whisper']['task']
        )

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        log_task(f"Transcription completed in {duration:.2f} seconds for {mp3_file}", unique_id)
        log_system_usage(unique_id)
        return {'text': result['text'], 'duration': duration}
    except Exception as e:
        logger.error(f"Failed to transcribe {mp3_file}: {e}", exc_info=True, extra={'unique_id': unique_id})
        log_error(f"Failed to transcribe {mp3_file}: {e}", unique_id)
        raise

# -----------------------------------------------------------------------------
# Async Function: process_file
# -----------------------------------------------------------------------------
async def process_file(mp3_file: str, department: str) -> None:
    """
    Processes the MP3 file, transcribes it, saves the result, and sends it via webhook.

    Args:
        mp3_file (str): The MP3 file to process (includes 'audio/' directory).
        department (str): The department associated with the audio.
    """
    base_path = config['ttd_transcribed_audio_Path']['base_path']
    delete_after_process = config['ttd_transcribed_Logging']['delete_after_processing']
    transcript_dir = os.path.join(script_dir, config['ttd_transcribed_Logging']['log_dir'], "transcripts")
    os.makedirs(transcript_dir, exist_ok=True)

    # Ensure .mp3 extension is present
    if not mp3_file.lower().endswith('.mp3'):
        mp3_file += '.mp3'

    # Normalize mp3_file to remove redundant separators
    mp3_file = os.path.normpath(mp3_file)

    # Construct full audio path without duplicating 'audio'
    full_audio_path = os.path.normpath(os.path.join(base_path, mp3_file))

    # Set unique_id to the original MP3 filename without the '.mp3' extension
    unique_id = os.path.splitext(os.path.basename(mp3_file))[0]
    duration: float = 0.0

    try:
        logger.debug(f"Base path: {base_path}", extra={'unique_id': unique_id})
        logger.debug(f"Full audio path: {full_audio_path}", extra={'unique_id': unique_id})

        if not os.path.isfile(full_audio_path):
            raise FileNotFoundError(f"MP3 file not found: {full_audio_path}")

        transcription_result = transcribe_audio(full_audio_path, department, unique_id)
        transcription = transcription_result['text']
        duration = transcription_result['duration']

        # Save transcript with original filename (without .mp3) and .txt extension
        transcript_file_path = os.path.join(transcript_dir, f"{unique_id}.txt")
        async with aiofiles.open(transcript_file_path, 'w') as f:
            await f.write(transcription)
        log_task(f"Transcription saved to: {transcript_file_path}", unique_id)

        # Send the transcription via webhook
        async with aiohttp.ClientSession() as session:
            success = await send_webhook(mp3_file, department, transcription, session, unique_id)
            if success and delete_after_process:
                os.remove(full_audio_path)
                log_task(f"Deleted processed file: {full_audio_path}", unique_id)

    except Exception as e:
        logger.error(f"Error processing file: {unique_id}: {e}", exc_info=True, extra={'unique_id': unique_id})
        log_error(f"Error processing {unique_id}: {e}", unique_id)
        await send_pushover_notification_async("Script Error", f"Error processing {unique_id}: {e}", priority=1)

    finally:
        # Ensure that grouped notifications are sent even if an error occurs
        await send_grouped_pushover_notifications(duration, unique_id)
        # Clear notifications after sending to prevent duplication
        task_notifications.clear()
        error_notifications.clear()

# -----------------------------------------------------------------------------
# Function: send_grouped_pushover_notifications
# -----------------------------------------------------------------------------
async def send_grouped_pushover_notifications(duration: float, unique_id: str) -> None:
    """
    Sends grouped Pushover notifications for tasks and errors.

    Args:
        duration (float): The duration of the transcription process in seconds.
        unique_id (str): The unique identifier for the task.
    """
    if task_notifications:
        timestamp = datetime.now().isoformat()
        detailed_message = f"""Transcription Task Summary
From Audio Workflow on {timestamp}
Task Summary:
- Timestamp: {timestamp}
- Duration: {duration:.2f} seconds
- Tasks:
{chr(10).join(task_notifications)}
"""
        await send_pushover_notification_async(
            title="Transcription Task Summary",
            message=detailed_message.strip(),
            priority=-1  # Low priority for normal activity
        )

    if error_notifications:
        detailed_error = "\n".join(error_notifications)
        await send_pushover_notification_async(
            title="Transcription Errors",
            message=detailed_error,
            priority=1  # Higher priority for errors
        )

# -----------------------------------------------------------------------------
# Function: detect_anomalies
# -----------------------------------------------------------------------------
def detect_anomalies() -> None:
    """
    Detects anomalies based on predefined thresholds and logs alerts.
    """
    process = psutil.Process(os.getpid())
    memory_usage_mb = process.memory_info().rss / (1024 * 1024)
    cpu_usage = process.cpu_percent(interval=1.5)

    # Example thresholds
    MEMORY_THRESHOLD_MB = 2000  # 2 GB
    CPU_THRESHOLD_PERCENT = 80.0  # 80%

    if memory_usage_mb > MEMORY_THRESHOLD_MB:
        logger.warning(f"High memory usage detected: {memory_usage_mb:.2f} MB", extra={'unique_id': 'N/A'})
        asyncio.create_task(send_pushover_notification_async(
            title="High Memory Usage",
            message=f"Memory usage is at {memory_usage_mb:.2f} MB, which exceeds the threshold of {MEMORY_THRESHOLD_MB} MB.",
            priority=1
        ))

    if cpu_usage > CPU_THRESHOLD_PERCENT:
        logger.warning(f"High CPU usage detected: {cpu_usage:.2f}%", extra={'unique_id': 'N/A'})
        asyncio.create_task(send_pushover_notification_async(
            title="High CPU Usage",
            message=f"CPU usage is at {cpu_usage:.2f}%, which exceeds the threshold of {CPU_THRESHOLD_PERCENT}%.",
            priority=1
        ))

# -----------------------------------------------------------------------------
# Function: transcribe_audio_with_anomaly_detection
# -----------------------------------------------------------------------------
def transcribe_audio_with_anomaly_detection(mp3_file: str, department: str, unique_id: str) -> Dict[str, Any]:
    """
    Transcribes an audio file using Whisper AI, detects anomalies, and logs performance metrics.
    
    Args:
        mp3_file (str): The path to the MP3 file to transcribe.
        department (str): The department associated with the audio.
        unique_id (str): The unique identifier for the task.
    
    Returns:
        Dict[str, Any]: A dictionary containing the transcription text and duration.
    """
    transcription_result = transcribe_audio(mp3_file, department, unique_id)
    duration = transcription_result['duration']
    
    # Detect anomalies after transcription
    detect_anomalies()
    
    return transcription_result

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
async def main() -> None:
    """
    Main function that handles script execution: parsing arguments, processing files, etc.
    """
    logger.debug("Starting ttd_transcribed script.", extra={'unique_id': 'N/A'})

    # Use argparse to handle command-line arguments
    parser = argparse.ArgumentParser(description="Transcribe audio files and send the result via webhook.")
    parser.add_argument("mp3_file", type=str, help="The MP3 file to transcribe (includes 'audio/' directory).")
    parser.add_argument("department", type=str, help="The department the file belongs to.")
    parser.add_argument("--log-level", type=str, help="Set the logging level (e.g., DEBUG, INFO).")

    args = parser.parse_args()

    # Override log level if specified
    if args.log_level:
        new_log_level = getattr(logging, args.log_level.upper(), logging.DEBUG)
        logger.setLevel(new_log_level)
        for handler in logger.handlers:
            handler.setLevel(new_log_level)
        logger.info(f"Logging level changed to {args.log_level.upper()}.", extra={'unique_id': 'N/A'})

    # Process the file with department name included
    await process_file(args.mp3_file, args.department)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Unexpected error in main execution: {e}", exc_info=True, extra={'unique_id': 'N/A'})
    finally:
        if GPU_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
                logger.info("pynvml shutdown successfully.", extra={'unique_id': 'N/A'})
            except pynvml.NVMLError as e:
                logger.error(f"Error shutting down pynvml: {e}", extra={'unique_id': 'N/A'})
        logger.info("Script terminated.", extra={'unique_id': 'N/A'})
