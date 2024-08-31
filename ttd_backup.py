import os
import logging
import shutil
import hashlib
from ftplib import FTP, error_perm
import configparser
from datetime import datetime, timedelta
import requests
import time

# -----------------------------------------------------------------------------
# Script Information
# -----------------------------------------------------------------------------
# Script Name: FTP Upload with Compression, Integrity Check, and Notifications Script
# Version: v1.6.2
# Author: Quentin King
# Date: 08-31-2024
# Description: This script compresses a specified directory into a ZIP file, uploads it to
#              an FTP server, verifies the integrity of the upload using MD5 hashing, manages
#              backup retention on the server (maximum of 10 backups and 10 days), and sends
#              notifications via Pushover. Logs are created in a subdirectory with filenames 
#              that include the date and time of the script execution.
# -----------------------------------------------------------------------------
# Changelog:
# - v1.6.2:
#   - Added debug statements to trace configuration loading and improve error handling.
#   - Corrected typo in the `download_file_from_ftp` function.
#   - Improved configuration integration and error handling.
# -----------------------------------------------------------------------------
# Configuration:
# - `BackupScript_Logging` section in config.ini:
#   - `log_dir`: Directory where logs will be stored (relative to the script's location).
#   - `max_logs`: Maximum number of log files to keep.
#   - `max_log_days`: Maximum age of log files (in days).
# - `BackupScript_Backup` section in config.ini:
#   - `source_directory`: Directory to be backed up.
#   - `temp_directory`: Directory where the temporary ZIP file will be stored.
#   - `retention_count`: Maximum number of backups to keep.
#   - `retention_days`: Maximum age of backups to keep (in days).
# - `BackupScript_FTP` section in config.ini:
#   - `server`: FTP server address.
#   - `port`: FTP server port.
#   - `user`: Username for FTP backup.
#   - `pass`: Password for FTP backup.
# - `BackupScript_Pushover` section in config.ini:
#   - `token`: Pushover API token for sending notifications.
#   - `user`: Pushover user key for sending notifications.
#   - `rate_limit_seconds`: Rate limiting interval for Pushover notifications (in seconds).
# -----------------------------------------------------------------------------

# Determine the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load configuration from the INI files
config = configparser.ConfigParser()

# Load credentials.ini first to resolve placeholders
credentials = configparser.ConfigParser()
credentials_file = os.path.join(script_dir, 'credentials.ini')
config_file = os.path.join(script_dir, 'config.ini')

print(f"Loading credentials from {credentials_file}")
credentials.read(credentials_file)

print(f"Loading configuration from {config_file}")
config.read_dict(credentials)  # Include credentials
config.read(config_file)

# Load script-specific settings
try:
    log_directory = config.get('BackupScript_Logging', 'log_dir')
    log_directory = os.path.join(script_dir, log_directory)  # Ensure the log directory is relative to the script's location
    max_logs = config.getint('BackupScript_Logging', 'max_logs', fallback=10)
    max_log_days = config.getint('BackupScript_Logging', 'max_log_days', fallback=10)

    source_directory = config.get('BackupScript_Backup', 'source_directory')
    temp_directory = config.get('BackupScript_Backup', 'temp_directory')
    backup_retention_count = config.getint('BackupScript_Backup', 'retention_count', fallback=10)
    backup_retention_days = config.getint('BackupScript_Backup', 'retention_days', fallback=10)

    ftp_server = config.get('BackupScript_FTP', 'server')
    ftp_port = config.getint('BackupScript_FTP', 'port')
    ftp_user = config.get('BackupScript_FTP', 'user')
    ftp_pass = config.get('BackupScript_FTP', 'pass')

    pushover_token = config.get('BackupScript_Pushover', 'token')
    pushover_user = config.get('BackupScript_Pushover', 'user')
    pushover_rate_limit = config.getint('BackupScript_Pushover', 'rate_limit_seconds', fallback=300)

    print("Configuration loaded successfully.")

except configparser.NoSectionError as e:
    print(f"Configuration error: {e}")
    raise
except configparser.NoOptionError as e:
    print(f"Configuration option error: {e}")
    raise
except Exception as e:
    print(f"Unexpected error loading configuration: {e}")
    raise

# Set up logging with a new file for each run in a subdirectory
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = os.path.join(log_directory, f'ftp_upload_{current_time}.log')

logging.basicConfig(
    filename=log_file,
    level=logging.DEBUG,  # Set to DEBUG to capture all levels of logs
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Rate limiting for Pushover notifications
last_pushover_time = 0

def send_pushover_notification(message, title="TTD Backup Script", priority=-1):
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
        "priority": priority
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
                    logging.warning(f"Retrying upload and verification for {local_file} (Attempt {attempt + 1})")
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

def main():
    """Main function to handle directory compression, file upload, integrity check, and retention management."""
    zip_file_path = os.path.join(temp_directory, 'TTD_Backup_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.zip')
    
    try:
        compress_directory_to_zip(source_directory, zip_file_path)

        ftp = connect_to_ftp()
        if not ftp:
            logging.critical("FTP connection failed. Exiting.")
            return

        upload_successful = upload_file_to_ftp(ftp, zip_file_path, os.path.basename(zip_file_path))

        if upload_successful:
            manage_backup_retention(ftp, '/')

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

if __name__ == "__main__":
    main()
