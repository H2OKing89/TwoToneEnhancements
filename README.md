Here’s an updated version of your `README.md` file, with additional information, version updates, and detailed setup instructions tailored for Windows. This should provide a comprehensive guide for anyone looking to get started with your project.

---

# TwoToneDetect Python Integration

## Version: 1.2.0  
**Date:** 2024-09-01  
**Author:** Quentin King

## Project Overview

This project provides Python scripts that extend the functionality of the TwoToneDetect software, which monitors audio from fire department dispatch radios. TwoToneDetect identifies specific tones that correspond to different fire departments and triggers predefined commands. This project includes two primary Python scripts:

1. **TwoToneDetect Pre-Notification**: Triggered immediately when tones are detected. It sends a webhook notification with information about the detected tones.
2. **TwoToneDetect Audio Notification**: Triggered after the dispatch audio has been recorded. This script uploads the audio file to an FTP server, verifies its integrity, and sends a webhook notification with the URL to the file.

These scripts are designed to integrate seamlessly with TwoToneDetect and provide enhanced notification and monitoring capabilities.

## Setup Instructions

### 1. Prerequisites

- **Python 3.6+**: Ensure Python is installed on your system. You can download it from [python.org](https://www.python.org/downloads/).
- **Python Libraries**: Install the required Python libraries:
  ```bash
  pip install requests configparser python-dotenv psutil
  ```

### 2. Configuration Files

The scripts rely on two configuration files: `config.ini` and `.env` to manage settings and credentials. These files should be placed in the same directory as the Python scripts.

#### **config.ini**

This file handles general configuration settings, including logging, retry mechanisms, file handling, and webhook settings.

```ini
# ----------------------------------------------------------------------
# Configuration Settings for ttd_audio_notification.py
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Base Audio Path to TTD Audio Files
# ----------------------------------------------------------------------
[ttd_audio_notification_Path]
base_path = C:\path\to\TwoToneDetect\  # Define the base directory where audio files are stored

# ----------------------------------------------------------------------
# Logging Settings
# ----------------------------------------------------------------------
[ttd_audio_notification_Logging]
log_dir = logs\ttd_audio_notification_logs  # Directory for storing log files
log_level = DEBUG  # Log level (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL)
max_logs = 5  # Maximum number of log files to retain
max_log_days = 10  # Number of days to retain log files
log_to_console = False  # Set to True to log messages to the console

# ----------------------------------------------------------------------
# Webhook Settings
# ----------------------------------------------------------------------
[ttd_audio_notification_Webhook]
ttd_audio_received_url = https://your-webhook-url/endpoint/ttd_audio_received  # Webhook URL for audio received events
base_audio_url = https://your-audio-url/audio/  # Base URL for accessing uploaded audio files
timeout_seconds = 10  # Timeout in seconds for webhook API requests

# ----------------------------------------------------------------------
# Pushover Notification Settings
# ----------------------------------------------------------------------
[ttd_audio_notification_Pushover]
priority = 1  # Pushover priority level (0=Lowest, 1=Low, 2=Normal, 3=High, 4=Emergency)
retry = 60  # Retry interval in seconds for emergency priority notifications
expire = 3600  # Expiration time in seconds for emergency priority notifications
sound = pushover  # Sound to play for the notification (options: pushover, bike, bugle, etc.)

# ----------------------------------------------------------------------
# Retry Logic Settings
# ----------------------------------------------------------------------
[ttd_audio_notification_Retry]
max_retries = 3  # Maximum number of retry attempts if a webhook fails
retry_delay = 5  # Initial delay in seconds before the first retry
backoff_strategy = exponential  # Retry strategy: linear, exponential, jitter

# ----------------------------------------------------------------------
# File Handling Settings
# ----------------------------------------------------------------------
[ttd_audio_notification_FileHandling]
temp_directory = temp_files  # Directory for storing temporary files
```

#### **.env**

This file contains sensitive credentials for FTP and Pushover services. Ensure this file is kept secure and not shared publicly.

```dotenv
# ----------------------------------------------------------------------
# FTP Credentials
# ----------------------------------------------------------------------
FTP_SERVER=your-ftp-server  # FTP server address
FTP_PORT=21  # FTP port
FTP_USER=your-ftp-username  # FTP username
FTP_PASS=your-ftp-password  # FTP password

# ----------------------------------------------------------------------
# Pushover API Credentials
# ----------------------------------------------------------------------
PUSHOVER_TOKEN=your_pushover_token  # Pushover API token
PUSHOVER_USER=your_pushover_user_key  # Pushover User Key
```

### 3. Internal Configuration (TwoToneDetect)

To integrate the Python scripts with TwoToneDetect, you need to modify the `tone.cfg` file that comes with TwoToneDetect.

#### **Pre-Notification Command**

Add the following line to trigger the pre-notification script:

```ini
alert_command = python "C:\path\to\your\ttd_pre_notification.py" [mp3] [d]
```

- **[mp3]**: Placeholder for the audio file name.
- **[d]**: Placeholder for the department name.

#### **Audio Notification and FTP Command**

Add the following line to trigger the audio upload and notification script:

```ini
post_email_command = python "C:\path\to\your\ttd_audio_notification.py" [mp3] [d]
```

- **[mp3]**: Placeholder for the audio file name.
- **[d]**: Placeholder for the department name.

### 4. Running the Scripts

#### **TwoToneDetect Pre-Notification**

This script is triggered immediately when tones are detected by TwoToneDetect.

```bash
python ttd_pre_notification.py <file_name> <topic>
```

- **file_name**: The name of the audio file (optional, can be any placeholder).
- **topic**: The department or topic for the notification.
- **--retries**: Number of retry attempts for sending the webhook (default is 3).

#### **TwoToneDetect Audio Notification**

This script is triggered after the dispatch audio has been recorded.

```bash
python ttd_audio_notification.py <file_name> <department>
```

- **file_name**: The path to the recorded audio file.
- **department**: The department name associated with the audio file.

### 5. Logging Information

- Logs are stored in the directory specified in the `log_dir` configuration (`logs\ttd_audio_notification_logs\` by default).
- Log files are rotated automatically based on the `max_logs` and `max_log_days` settings.
- The log file naming convention is `ftp_upload_MM-DD-YYYY_HH-MM-SS.log`.

### 6. Versioning and Changelog

Versioning is managed within each script, with updates noted in the changelog at the top of each file. The current versions are:

- **TwoToneDetect Pre-Notification**: v1.8.0
- **TwoToneDetect Audio Notification**: v2.0.0
- **FTP Upload with Compression, Integrity Check, and Notifications Script**: v1.6.0

### 7. Security Considerations

- **Sensitive Information**: API tokens and other sensitive credentials are stored in the `.env` file. Ensure this file is kept secure and not exposed to unauthorized users.
- **Configuration Files**: Only share sanitized versions of the `.ini` files that do not contain sensitive information. Use placeholders in public versions.
- **Access Control**: Ensure that only trusted individuals have access to the scripts and configuration files, especially those that contain sensitive credentials.

## Troubleshooting

### Common Issues

1. **Missing Configuration Files**: Ensure that all required `.ini` and `.env` files are in the same directory as the Python scripts.
2. **Invalid Credentials**: Double-check the FTP and Pushover API credentials in the `.env` file if notifications are not being sent or if FTP uploads are failing.
3. **File Paths**: Ensure that the paths specified in `config.ini` are correct and accessible.
4. **Python Version**: Ensure that you are using Python 3.6 or later.

### Dependencies and Python Version

- **Dependencies**: The scripts require the following Python libraries, which can be installed via pip:
  - `requests`: For making HTTP requests to webhooks and APIs.
  - `configparser`: For parsing configuration files.
  - `python-dotenv`: For loading environment variables from a `.env` file.
  - `psutil`: For performance monitoring and resource usage tracking.
- **Python Version**: The scripts are compatible with Python 3.6 and later.

## Additional Information

### Running on Windows

- Ensure that Python is properly installed and added to your system’s PATH.
- The scripts are designed to run seamlessly on Windows, but they should be adaptable for other operating systems with minor adjustments.
- The setup and running instructions provided above are tailored specifically for a Windows environment.

### Future Enhancements

- **Additional Error Handling**: Plan to add more granular error handling for specific edge cases.
- **Cross-Platform Compatibility**: Potential updates to ensure full compatibility across different operating systems (e.g., Linux, macOS).
- **Advanced Logging**: Future versions may include more advanced logging capabilities, including integration with logging services or centralized logging solutions.

---

This `README.md` file should now provide clear and comprehensive instructions for setting up and running the TwoToneDetect Python integration, particularly for users on Windows. If there’s anything more you’d like to add or adjust, feel free to let me know!