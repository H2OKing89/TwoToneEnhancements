import os
import sys
import logging
import requests  # Import requests module
import whisper
import configparser
from datetime import datetime
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: ttd_transcribed.py
# Version: v1.2.0
# Author: Quentin King
# Creation Date: 09-06-2024
# Description: 
# This script transcribes audio files using the Whisper AI model and sends a webhook to Node-RED 
# with the transcription details. The Whisper model size, accuracy parameters, and other settings 
# are dynamically loaded from a configuration file (config.ini) for flexibility.
# -----------------------------------------------------------------------------
# Changelog:
# - v1.2.0 (09-06-2024): Added support for full Whisper settings via config.ini, including 
#                        beam_size, best_of, and initial_prompt. Added error handling for missing
#                        config values and improved modularity for future updates.
# - v1.1.0 (09-06-2024): Added support for model size and temperature settings from config.ini.
# - v1.0.1 (09-06-2024): Initial version with logging and transcription processing.
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
log_dir = os.path.join(script_dir, config['ttd_transcribed_Logging']['log_dir'])
log_level = config['ttd_transcribed_Logging']['log_level']
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

# Access the Base Path for Audio Files
base_path = config['ttd_transcribed_audio_Path']['base_path']

# Ensure the log directory exists
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logging
log_file_name = f"ttd_transcribed_{datetime.now().strftime('%m-%d-%Y_%H-%M-%S')}.log"
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
# Function: transcribe_audio
# -----------------------------------------------------------------------------
def transcribe_audio(mp3_file):
    # Load the Whisper model based on the configuration
    logging.info(f"Loading Whisper model: {model_size} with temperature: {temperature}")
    model = whisper.load_model(model_size)

    # Transcribe the audio file with the configured settings (without 'timestamps')
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
    
    # Return the transcription
    return result['text']


# -----------------------------------------------------------------------------
# Function: send_webhook
# -----------------------------------------------------------------------------
def send_webhook(mp3_file, department, transcription):
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
    logging.debug("Starting ttd_transcribed script.")
    
    try:
        if len(sys.argv) != 3:
            raise ValueError(f"Expected 3 arguments (script name, mp3 file, department), but got {len(sys.argv)}.")

        mp3_file = os.path.join(base_path, sys.argv[1])
        department = sys.argv[2]
        logging.info(f"Processing transcription for file: {mp3_file}, Department: {department}")

        if os.path.isfile(mp3_file):
            # Transcribe the audio file
            transcription = transcribe_audio(mp3_file)
            logging.info(f"Transcription completed for {mp3_file}: {transcription}")

            # Send the transcription via webhook
            send_webhook(mp3_file, department, transcription)
        else:
            raise FileNotFoundError(f"MP3 file not found: {mp3_file}")
    
    except ValueError as ve:
        logging.error(f"ValueError encountered: {ve}")
    except FileNotFoundError as fnf_error:
        logging.error(f"FileNotFoundError encountered: {fnf_error}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        logging.info("ttd_transcribed script completed.")

if __name__ == "__main__":
    main()
