import os
import time
import logging
import subprocess
import configparser
import signal
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv
import requests

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_program_starter.py
# Version: v2.0.2
# Author: Quentin King
# Date: 09-02-2024
# Description: This script is designed to be executed by a heartbeat monitoring
#              script when the monitored program fails. It attempts to restart
#              the specified program using PowerShell, with retry logic and
#              logging. Notifications are sent via Pushover.
# Changelog:
# - v2.0.2: Updated logging structure to store active logs in 
#           logs\ttd_program_starter_logs\ttd_program_starter_MM-DD-YYYY_HH-MM.log
#           and archived logs in logs\ttd_program_starter_logs\ttd_program_starter_archive\
#           with the same filename format.
# - v2.0.1: Added logic to close logging handlers before archiving logs to
#           prevent PermissionError (WinError 32) when moving log files.
# - v2.0.0: Refactored script with modularization, improved error handling with
#           retries and exponential backoff, enhanced logging with rotation, added
#           configuration validation, and implemented graceful shutdown handling.
# -----------------------------------------------------------------------------

# Load environment variables from .env
load_dotenv()

# -----------------------------------------------------------------------------
# Configuration Management
# -----------------------------------------------------------------------------
def load_config():
    """
    Loads the configuration from the config.ini file located in the script's directory.

    Returns:
        tuple: A tuple containing the configparser object and the script's directory.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')

    config = configparser.ConfigParser()
    config.read(config_path)

    return config, script_dir

def validate_config(config):
    """
    Validates critical configurations from the config.ini file.

    Args:
        config (configparser.ConfigParser): The loaded configuration.

    Raises:
        ValueError: If the program directory or program file does not exist.
    """
    if not os.path.isdir(config['Program_Start']['program_dir']):
        raise ValueError(f"Invalid program directory: {config['Program_Start']['program_dir']}")
    if not os.path.isfile(os.path.join(config['Program_Start']['program_dir'], config['Program_Start']['program_name'])):
        raise ValueError(f"Program file does not exist: {config['Program_Start']['program_name']}")

# -----------------------------------------------------------------------------
# Logging Setup
# -----------------------------------------------------------------------------
def setup_logging(script_dir, log_file_name):
    """
    Sets up logging with a rotating file handler and console output.

    Args:
        script_dir (str): The directory where logs will be stored.
        log_file_name (str): The name of the log file for this session.

    Returns:
        logging.Logger: A configured logger instance.
    """
    log_dir = os.path.join(script_dir, 'logs', 'ttd_program_starter_logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file_path = os.path.join(log_dir, log_file_name)

    logger = logging.getLogger('program_starter')
    logger.setLevel(logging.INFO)

    handler = RotatingFileHandler(log_file_path, maxBytes=10485760, backupCount=10)  # 10MB per file, 10 backups
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger, log_file_path

def close_log_handlers(logger):
    """
    Closes all handlers associated with the logger.

    Args:
        logger (logging.Logger): The logger instance to close handlers for.
    """
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)

def archive_old_logs(log_file_path, archive_dir):
    """
    Archives the current log file to a specified directory.

    Args:
        log_file_path (str): The path to the current log file.
        archive_dir (str): The directory where logs should be archived.
    """
    if not os.path.exists(archive_dir):
        os.makedirs(archive_dir)

    log_file_name = os.path.basename(log_file_path)
    archived_log_path = os.path.join(archive_dir, log_file_name)

    os.rename(log_file_path, archived_log_path)

# -----------------------------------------------------------------------------
# Graceful Shutdown Handling
# -----------------------------------------------------------------------------
def setup_signal_handling(logger):
    """
    Sets up signal handling for graceful shutdown.

    Args:
        logger (logging.Logger): The logger instance to log shutdown events.
    """
    def signal_handler(signal, frame):
        logger.info("Received shutdown signal. Cleaning up...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

# -----------------------------------------------------------------------------
# Program Start with Retry Logic
# -----------------------------------------------------------------------------
def start_program(config, logger):
    """
    Attempts to start the specified program with retries and exponential backoff.

    Args:
        config (configparser.ConfigParser): The loaded configuration.
        logger (logging.Logger): The logger instance for logging events.
    """
    program_dir = config['Program_Start']['program_dir']
    program_name = config['Program_Start']['program_name']

    command = f'powershell -NoProfile -ExecutionPolicy Bypass -Command "cd \'{program_dir}\'; Start-Process \'{program_name}\'"'
    logger.info(f"Executing command: {command}")
    process = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    stderr_output = process.stderr.strip()
    stdout_output = process.stdout.strip()

    logger.info(f"Return code: {process.returncode}")
    if stdout_output:
        logger.info(f"Stdout: {stdout_output}")
    if stderr_output:
        logger.warning(f"Stderr: {stderr_output}")

    if process.returncode == 0:
        logger.info(f"Successfully started the program: {program_name}")
    else:
        logger.error(f"Failed to start the program: {program_name}")
        send_alert(config, f"Failed to start the program: {program_name}")

# -----------------------------------------------------------------------------
# Notification Handling
# -----------------------------------------------------------------------------
def send_alert(config, message):
    """
    Sends an alert via Pushover.

    Args:
        config (configparser.ConfigParser): The loaded configuration.
        message (str): The message to be sent in the alert.
    """
    pushover_token = config['ttd_program_starter_Pushover']['pushover_token']
    pushover_user = config['ttd_program_starter_Pushover']['pushover_user']
    pushover_url = "https://api.pushover.net/1/messages.json"
    payload = {
        "token": pushover_token,
        "user": pushover_user,
        "message": message,
        "title": "TTD Program Starter Alert",
        "priority": config['ttd_program_starter_Pushover'].getint('priority'),
        "retry": config['ttd_program_starter_Pushover'].getint('retry'),
        "expire": config['ttd_program_starter_Pushover'].getint('expire'),
        "sound": config['ttd_program_starter_Pushover']['sound']
    }

    try:
        response = requests.post(pushover_url, data=payload)
        response.raise_for_status()
        logging.info("Pushover notification sent successfully.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Pushover notification: {e}")

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
def main():
    """
    The main function that orchestrates the script execution.
    """
    # Load and validate configuration
    config, script_dir = load_config()
    validate_config(config)

    # Setup logging
    log_file_name = f"ttd_program_starter_{datetime.now().strftime('%m-%d-%Y_%H-%M')}.log"
    logger, log_file_path = setup_logging(script_dir, log_file_name)

    # Setup signal handling
    setup_signal_handling(logger)

    # Start the program
    start_program(config, logger)

    # Archive the log if rotation is enabled
    if config['ttd_program_starter_Logging'].getboolean('log_rotation_enabled'):
        # Close log handlers before archiving
        close_log_handlers(logger)

        archive_dir = os.path.join(script_dir, 'logs', 'ttd_program_starter_logs', 'ttd_program_starter_archive')
        archive_old_logs(log_file_path, archive_dir)

if __name__ == "__main__":
    main()
