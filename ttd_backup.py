import os
import logging
import shutil
import hashlib
from ftplib import FTP, error_perm
import configparser
from datetime import datetime, timedelta
import requests
import time
import signal
import sys

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: FTP Upload with Compression, Integrity Check, and Notifications Script
# Version: v1.6.1
# Author: Quentin King
# Date: 09-01-2024
# Description: This script compresses a specified directory into a ZIP file, uploads it to
#              an FTP server, verifies the integrity of the upload using MD5 hashing, manages
#              backup retention on the server, deletes audio files in a subdirectory, 
#              and sends notifications via Pushover. Logs are created in a subdirectory with
#              filenames that include the date and time of the script execution.
# -----------------------------------------------------------------------------
# Changelog:
# - v1.6.1:
#   - Fixed the issue with FTP using the wrong part from the .env file.
# - v1.6.0:
#   - Moved sensitive credentials to environment variables for better security.
#   - Added detailed comments and modularized the script further.
#   - Enhanced error notifications and added execution time logging.
# - v1.5.5:
#   - Modified verification process to download and verify the backup file instead of 
#     using modification time.
#   - Fixed issue where FTP was not passed correctly to perform_backup_verification.
#   - Enhanced error notifications and added execution time logging.
# - v1.5.4:
#   - Added log file retention management. Logs will be deleted based on maximum number
#     of log files and/or maximum log file age, whichever comes first.
# -----------------------------------------------------------------------------

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Determine the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load configuration from a file in the same directory as the script
config_file_path = os.path.join(script_dir, 'config.ini')
config = configparser.ConfigParser()
config.read(config_file_path)

# Load script-specific settings
log_directory = config.get('BackupScript_Logging', 'log_dir')
log_directory = os.path.join(script_dir, log_directory)
max_logs = config.getint('BackupScript_Logging', 'max_logs', fallback=10)
max_log_days = config.getint('BackupScript_Logging', 'max_log_days', fallback=10)

source_directory = config.get('BackupScript_Backup', 'source_directory')
temp_directory = config.get('BackupScript_Backup', 'temp_directory')
backup_retention_count = config.getint('BackupScript_Backup', 'retention_count', fallback=10)
backup_retention_days = config.getint('BackupScript_Backup', 'retention_days', fallback=10)
backup_verification_interval_days = config.getint('BackupScript_Backup', 'backup_verification_interval_days', fallback=7)

# Access FTP credentials from environment variables
ftp_server = os.getenv('BACKUP_FTP_SERVER')
ftp_port = int(os.getenv('BACKUP_FTP_PORT'))
ftp_user = os.getenv('BACKUP_FTP_USER')
ftp_pass = os.getenv('BACKUP_FTP_PASS')

# Access Pushover credentials from environment variables
pushover_token = os.getenv('PUSHOVER_TOKEN')
pushover_user = os.getenv('PUSHOVER_USER')
pushover_rate_limit = config.getint('BackupScript_Pushover', 'rate_limit_seconds', fallback=300)
pushover_priority = config.getint('BackupScript_Pushover', 'priority', fallback=1)
pushover_retry = config.getint('BackupScript_Pushover', 'retry', fallback=60)
pushover_expire = config.getint('BackupScript_Pushover', 'expire', fallback=3600)
pushover_sound = config.get('BackupScript_Pushover', 'sound', fallback='pushover')

# Set up logging with a new file for each run in a subdirectory
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

current_time = datetime.now().strftime('%m-%d-%Y_%H-%M-%S')
log_file = os.path.join(log_directory, f'ftp_upload_{current_time}.log')

logging.basicConfig(
    filename=log_file,
    level=logging.DEBUG,  # Set to DEBUG to capture all levels of logs
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Rate limiting for Pushover notifications
last_pushover_time = 0

def send_pushover_notification(message, title="TTD Backup Script", priority=pushover_priority):
    """Send a notification to Pushover with rate limiting."""
    global last_pushover_time
    current_time = time.time()

    if current_time - last_pushover_time < pushover_rate_limit:
        logging.info("Pushover notification suppressed due to rate limiting.")
        return

    last_pushover_time = current_time
    data = {
        "token": pushover_token,
        "user": pushover_user,
        "message": message,
        "title": title,
        "priority": priority,
        "retry": pushover_retry,
        "expire": pushover_expire,
        "sound": pushover_sound
    }

    try:
        response = requests.post("https://api.pushover.net/1/messages.json", data=data)
        response.raise_for_status()
        logging.info("Pushover notification sent successfully.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Pushover notification: {e}")

def calculate_md5(file_path):
    """Calculate the MD5 hash of a file for integrity verification."""
    logging.info(f"Calculating MD5 hash for {file_path}")
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logging.error(f"Failed to calculate MD5 hash: {e}")
        send_pushover_notification(f"Failed to calculate MD5 hash: {e}", priority=1)
        raise

def connect_to_ftp():
    """Establish a connection to the FTP server."""
    try:
        ftp = FTP()
        ftp.connect(ftp_server, ftp_port)
        ftp.login(ftp_user, ftp_pass)
        logging.info(f"Connected to FTP server {ftp_server}:{ftp_port}")
        return ftp
    except Exception as e:
        logging.error(f"Failed to connect to FTP server: {e}")
        send_pushover_notification(f"FTP connection failed: {e}", priority=1)
        return None

def delete_audio_files(source_dir):
    """Delete all audio files in the audio subdirectory."""
    audio_dir = os.path.join(source_dir, 'audio')
    if os.path.exists(audio_dir):
        logging.info(f"Deleting audio files in {audio_dir}...")
        for root, _, files in os.walk(audio_dir):
            for file in files:
                file_path = os.path.join(root, file)
                os.remove(file_path)
                logging.info(f"Deleted audio file: {file_path}")
    else:
        logging.info(f"No audio directory found at {audio_dir} to delete.")

def compress_directory_to_zip(source_dir, output_zip):
    """Compress the source directory into a ZIP file."""
    logging.info(f"Compressing directory {source_dir} into {output_zip}")
    try:
        shutil.make_archive(output_zip.replace('.zip', ''), 'zip', source_dir)
        logging.info(f"Directory {source_dir} compressed into {output_zip}")
    except Exception as e:
        logging.error(f"Failed to compress directory: {e}")
        send_pushover_notification(f"Compression failed: {e}", priority=1)
        raise

def download_file_from_ftp(ftp, remote_file, local_file):
    """Download a file from the FTP server."""
    try:
        with open(local_file, 'wb') as f:
            ftp.retrbinary(f'RETR {remote_file}', f.write)
        logging.info(f"Downloaded {remote_file} from FTP server to {local_file}")
    except Exception as e:
        logging.error(f"Failed to download {remote_file} from FTP server: {e}")
        send_pushover_notification(f"Download failed: {e}", priority=1)
        raise

def upload_file_to_ftp(ftp, local_file, remote_file, retries=1):
    """Upload a file to the FTP server and verify its integrity with a retry mechanism."""
    attempt = 0
    while attempt <= retries:
        try:
            local_md5 = calculate_md5(local_file)

            with open(local_file, 'rb') as f:
                ftp.storbinary(f'STOR {remote_file}', f)
            logging.info(f"Uploaded {local_file} to FTP server as {remote_file}")

            # Download the file back from the FTP server to verify its integrity
            downloaded_file = f"{os.path.splitext(local_file)[0]}_downloaded.zip"
            download_file_from_ftp(ftp, remote_file, downloaded_file)

            # Verify file integrity after upload by comparing MD5 hashes
            remote_md5 = calculate_md5(downloaded_file)

            if local_md5 == remote_md5:
                logging.info(f"MD5 hash verified for {remote_file}")
                os.remove(downloaded_file)
                logging.info(f"Temporary file {downloaded_file} deleted after verification.")
                return True
            else:
                logging.error(f"MD5 hash mismatch for {remote_file}")
                os.remove(downloaded_file)
                attempt += 1
                if attempt <= retries:
                    logging.warning(f"Retrying upload and verification for {local_file} (Attempt {attempt})")
                else:
                    break

        except Exception as e:
            logging.error(f"Failed to upload {local_file} to FTP server: {e}")
            attempt += 1
            if attempt > retries:
                break

    logging.critical(f"Failed to upload and verify {local_file} after {retries + 1} attempts.")
    send_pushover_notification(f"Critical error: MD5 mismatch for {remote_file} after {retries + 1} attempts", priority=1)
    return False

def manage_backup_retention(ftp, ftp_root):
    """Manage backup retention on the FTP server, keeping only the latest backups as specified."""
    try:
        ftp.cwd(ftp_root)
        backups = sorted(ftp.nlst(), reverse=True)

        # Filter out non-backup files and directories
        backups = [f for f in backups if f.endswith('.zip')]

        # Check backup count and delete older backups if necessary
        while len(backups) > backup_retention_count:
            old_backup = backups.pop()
            logging.info(f"Deleting old backup: {old_backup}")
            try:
                ftp.delete(old_backup)
                logging.info(f"Deleted backup: {old_backup}")
            except error_perm as e:
                logging.error(f"Failed to delete backup {old_backup}: {e}")

        # Check backup age and delete backups older than the retention period
        current_time = datetime.now()
        for backup in backups:
            modified_time = ftp.sendcmd(f'MDTM {backup}')[4:].strip()
            modified_time = datetime.strptime(modified_time, '%Y%m%d%H%M%S')

            if current_time - modified_time > timedelta(days=backup_retention_days):
                logging.info(f"Deleting backup older than {backup_retention_days} days: {backup}")
                try:
                    ftp.delete(backup)
                    logging.info(f"Deleted backup: {backup}")
                except error_perm as e:
                    logging.error(f"Failed to delete backup {backup}: {e}")

    except Exception as e:
        logging.error(f"Failed to manage backup retention: {e}")
        send_pushover_notification(f"Backup retention failed: {e}", priority=1)

def manage_log_retention(log_dir, max_logs, max_days):
    """Delete logs based on the maximum number of logs and maximum log file age."""
    logs = sorted(os.listdir(log_dir))
    current_time = datetime.now()

    # Delete logs based on age
    for log in logs:
        log_path = os.path.join(log_dir, log)
        log_time = datetime.fromtimestamp(os.path.getmtime(log_path))
        if current_time - log_time > timedelta(days=max_days):
            os.remove(log_path)
            logging.info(f"Deleted old log file based on age: {log}")

    # Re-sort logs after deleting old ones
    logs = sorted(os.listdir(log_dir))

    # Delete logs based on number of files
    while len(logs) > max_logs:
        oldest_log = logs.pop(0)
        os.remove(os.path.join(log_dir, oldest_log))
        logging.info(f"Deleted old log file based on count: {oldest_log}")

def perform_backup_verification(ftp, remote_file, local_temp_dir):
    """Verify the integrity of the backup file stored on the FTP server by comparing MD5 hashes."""
    try:
        logging.info(f"Verifying integrity of the backup file {remote_file} on FTP server.")
        
        # Download the backup file from the FTP server
        temp_download_path = os.path.join(local_temp_dir, f"{remote_file}_verification")
        download_file_from_ftp(ftp, remote_file, temp_download_path)
        
        # Calculate the MD5 hash of the downloaded file
        local_md5 = calculate_md5(temp_download_path)
        remote_md5 = calculate_md5(os.path.join(local_temp_dir, remote_file))
        
        if local_md5 == remote_md5:
            logging.info(f"MD5 hash verification successful for {remote_file}.")
        else:
            logging.error(f"MD5 hash verification failed for {remote_file}.")
            raise ValueError("MD5 hash mismatch during backup verification.")
        
        # Clean up the temporary verification file
        os.remove(temp_download_path)
        logging.info(f"Temporary verification file {temp_download_path} deleted after verification.")
    
    except Exception as e:
        logging.critical(f"Failed to verify backup integrity: {e}", exc_info=True)
        send_pushover_notification(f"Backup verification failed for {remote_file}: {e}", priority=1)

def graceful_shutdown(signum, frame):
    """Handle graceful shutdown on receiving a signal."""
    logging.info("Received termination signal. Shutting down gracefully...")
    send_pushover_notification("Backup script terminated gracefully.")
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

def main():
    """Main function to handle directory compression, file upload, integrity check, and retention management."""
    zip_file_path = os.path.join(temp_directory, 'TTD_Backup_' + datetime.now().strftime('%m-%d-%Y_%H-%M-%S') + '.zip')
    
    start_time = datetime.now()
    
    try:
        # Delete audio files before compression
        delete_audio_files(source_directory)
        
        # Compress the directory
        compress_directory_to_zip(source_directory, zip_file_path)

        # Connect to FTP server
        ftp = connect_to_ftp()
        if not ftp:
            logging.critical("FTP connection failed. Exiting.")
            return

        # Upload the file to FTP and verify
        upload_successful = upload_file_to_ftp(ftp, zip_file_path, os.path.basename(zip_file_path))

        if upload_successful:
            manage_backup_retention(ftp, '/')

            # Perform verification
            perform_backup_verification(ftp, os.path.basename(zip_file_path), temp_directory)

        try:
            ftp.quit()
        except Exception as e:
            logging.error(f"Failed to properly close the FTP connection: {e}")

        # Manage log retention after processing
        manage_log_retention(log_directory, max_logs, max_log_days)

    except Exception as e:
        logging.critical(f"Unexpected critical error: {e}", exc_info=True)
        send_pushover_notification(f"Critical error: {e}", priority=1)

    finally:
        # Clean up the local ZIP file after upload
        if os.path.exists(zip_file_path):
            os.remove(zip_file_path)
            logging.info(f"Temporary file {zip_file_path} deleted.")
        
        # Log the script execution time
        end_time = datetime.now()
        execution_time = end_time - start_time
        logging.info(f"Script completed in {execution_time} seconds.")

        # Send final pushover notification on completion
        send_pushover_notification("Backup script completed successfully.")

if __name__ == "__main__":
    main()
