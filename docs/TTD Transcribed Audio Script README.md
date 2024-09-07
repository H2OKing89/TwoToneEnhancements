
---

# **TTD Transcribed Audio Script**

### Version: v1.8.2
### Author: Quentin King
### Creation Date: 09-07-2024

---

## **Table of Contents**
- [Description](#description)
- [Features](#features)
- [Requirements](#requirements)
- [Setup](#setup)
  - [1. Clone Repository](#1-clone-repository)
  - [2. Install Dependencies](#2-install-dependencies)
  - [3. Configuration](#3-configuration)
  - [4. Environment Variables](#4-environment-variables)
  - [5. Running the Script](#5-running-the-script)
- [Configuration Options](#configuration-options)
- [Logging](#logging)
- [Error Handling](#error-handling)
- [Prometheus Metrics](#prometheus-metrics)
- [Future Improvements](#future-improvements)

---

## **Description**

This script is designed to transcribe audio files using OpenAI's Whisper AI and send transcription data to a specified Node-RED webhook. The script supports error handling, retry logic, Pushover notifications, rate limiting, performance monitoring, and persistent state recovery. It is optimized for handling large volumes of audio files in an automated system.

---

## **Features**

- **Whisper AI Transcription:** Transcribes audio files using the Whisper model.
- **Webhook Integration:** Sends transcription results to a Node-RED webhook.
- **Pushover Notifications:** Sends notifications for critical errors and webhook failures.
- **Retry Logic:** Implements retry with exponential backoff for failed webhook transmissions.
- **Performance Monitoring:** Logs CPU and memory usage before and after transcriptions.
- **Custom Metrics:** Tracks transcription and webhook success/failure via Prometheus metrics.
- **Persistent State:** Recovers from failures to resume the transcription process.
- **Rate Limiting:** Includes rate limiting for both Pushover and webhooks to prevent overloading services.
- **Log Cleanup:** Automatically deletes old logs based on time or count retention strategies.
- **Cross-Platform Compatibility:** Works across Linux, macOS, and Windows environments.

---

## **Requirements**

- **Python 3.8+**
- **Whisper AI** (`whisper`)
- **aiohttp** (`aiohttp`)
- **requests** (`requests`)
- **ratelimit** (`ratelimit`)
- **python-dotenv** (`dotenv`)
- **psutil** (`psutil`)
- **prometheus_client** (`prometheus_client`)

---

## **Setup**

### **1. Clone Repository**

Clone the project repository to your local machine:

```bash
git clone https://github.com/username/ttd_transcribed.git
cd ttd_transcribed
```

### **2. Install Dependencies**

Install the required Python packages using `pip`:

```bash
pip install -r requirements.txt
```

### **3. Configuration**

A `config.ini` file is used to configure various options for the script. The file should be located in the root directory of the project.

Create or modify the `config.ini` file with the following structure:

```ini
[ttd_transcribed_Logging]
log_dir = ./logs
log_level = DEBUG
console_log_level = INFO
log_to_console = True

[ttd_transcribed_FileHandling]
delete_after_process = False

[ttd_transcribed_Whisper]
model_size = base
temperature = 0.7
timestamps = True
language = en
beam_size = 5
best_of = 3
no_speech_threshold = 0.6
compression_ratio_threshold = 2.4
logprob_threshold = -1.0
initial_prompt = None
condition_on_previous_text = True
verbose = False
task = transcribe

[ttd_transcribed_Webhook]
ttd_transcribed_url = http://localhost:1880/transcriptions
base_audio_url = http://localhost/audio/
timeout_seconds = 10

[ttd_transcribed_Retry]
retry_limit = 3
retry_delay = 5

[ttd_transcribed_Pushover]
priority = 1
rate_limit_seconds = 300
```

### **4. Environment Variables**

Create a `.env` file to store sensitive credentials (e.g., Pushover tokens):

```bash
touch .env
```

Add your environment variables to the `.env` file:

```env
PUSHOVER_TOKEN=<your-pushover-token>
PUSHOVER_USER=<your-pushover-user-key>
```

### **5. Running the Script**

Run the script with the following command:

```bash
python ttd_transcribed.py <mp3_file> <department>
```

- **mp3_file:** The path to the MP3 file to transcribe.
- **department:** The department the file belongs to (used for titles and notifications).

Example:

```bash
python ttd_transcribed.py audio.mp3 "Sales Department"
```

---

## **Configuration Options**

- **Whisper AI Settings:** Set model size, language, and various transcription settings via the `config.ini` file.
- **Logging:** Configure log levels and retention strategies in `config.ini` and `.env`.
- **Webhook:** Define the target webhook URL, retry settings, and rate limits.

---

## **Logging**

- **File Logs:** All log files are stored in the `./logs` directory.
- **Console Logs:** You can enable or disable console logs via the `log_to_console` setting.
- **Log Cleanup:** Old logs are cleaned up based on the retention strategy defined in the `config.ini` file.

---

## **Error Handling**

- **Retry Logic:** Webhook requests are retried with exponential backoff (based on `retry_limit` and `retry_delay` in `config.ini`).
- **Pushover Notifications:** Critical errors are sent to Pushover for immediate attention.
- **Persistent State:** The script saves state to recover from errors (e.g., system crashes) and resume the process.

---

## **Prometheus Metrics**

Custom Prometheus metrics are included to track transcription and webhook success/failure:

- **`transcription_success_total`**: Tracks successful transcriptions.
- **`transcription_failure_total`**: Tracks failed transcriptions.
- **`webhook_success_total`**: Tracks successful webhook requests.
- **`webhook_failure_total`**: Tracks failed webhook requests.

---

## **Future Improvements**

- Add batch processing for multiple audio files.
- Integrate support for additional AI models and transcription services.
- Implement advanced error handling for more granular recovery.
- Add more robust monitoring and reporting via Prometheus and Grafana.

---

## **License**

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## **Feedback**

If you encounter any issues or have suggestions for improvements, feel free to reach out or submit a pull request!

---

