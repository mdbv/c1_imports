#!/usr/bin/env bash

# Variables from environment variables with defaults
LOG_DIR="${LOG_DIR:-/var/log}"

# Check if the log directory exists
if [ ! -d "${LOG_DIR}" ]; then
    mkdir -p ${LOG_DIR}
fi

# Timestamped log file
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="${LOG_DIR}/sftp_sync_${TIMESTAMP}.log"
LATEST_LOG_LINK="${LOG_DIR}/sftp_sync_latest.log"
ln -sf ${LOG_FILE} ${LATEST_LOG_LINK}
ln -sf ${LOG_FILE} ${LOCAL_DIR}/sftp_sync_latest.log

# SFTP connection details
SFTP_HOST="${SFTP_HOST}"
SFTP_PORT="${SFTP_PORT:-22}"
SFTP_USER="${SFTP_USER}"
SFTP_PASS="${SFTP_PASS}"
SFTP_REMOTE_DIR="${SFTP_REMOTE_DIR:-.}"
LOCAL_DIR="${LOCAL_DIR:-/app/files}"

# SMB connection details
SMB_HOST="${SMB_HOST}"
SMB_SHARE="${SMB_SHARE}"
SMB_PATH="${SMB_PATH:-/}"
SMB_USER="${SMB_USER}"
SMB_PASS="${SMB_PASS}"

# Track performance
start_time=$(date +%s)

# Enhanced logging
log() {
    local level=$1
    local message=$2
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${level}] ${message}" >> ${LOG_FILE}
}

# Function to handle errors with alerting
handle_error() {
    local error_message=$1
    log "ERROR" "$error_message"
    # Add alerting here if needed
    exit 1
}

# Function to check if required variables are set
check_vars() {
    for var in "$@"; do
        if [ -z "${!var}" ]; then
            handle_error "Required variable $var is not set"
        fi
    done
}

# Function to retry failed operations
retry_operation() {
    local max_attempts=3
    local attempt=1
    local delay=5
    local cmd="$1"
    local log_cmd="$2"

    while [ $attempt -le $max_attempts ]; do
        log "INFO" "Attempt $attempt of $max_attempts: ${log_cmd:-$cmd}"
        eval "$cmd"

        if [ $? -eq 0 ]; then
            return 0
        fi

        log "WARN" "Attempt $attempt failed, retrying in $delay seconds..."
        sleep $delay
        attempt=$((attempt + 1))
        delay=$((delay * 2))  # Exponential backoff
    done

    return 1
}

# Create local directory if it doesn't exist
if [ ! -d "${LOCAL_DIR}" ]; then
    mkdir -p ${LOCAL_DIR}
fi

# Log start of sync
log "INFO" "Starting SFTP sync from ${SFTP_HOST}:${SFTP_REMOTE_DIR}"

# Check required SFTP variables
check_vars "SFTP_HOST" "SFTP_USER" "SFTP_PASS"

# Execute the SFTP command with retry
SFTP_CMD="lftp -u ${SFTP_USER},${SFTP_PASS} sftp://${SFTP_HOST}:${SFTP_PORT} -e \"set sftp:auto-confirm yes; mirror --verbose --only-newer --delete ${SFTP_REMOTE_DIR} ${LOCAL_DIR}; quit\" >> ${LOG_FILE} 2>&1"
SFTP_LOG_CMD="lftp -u ${SFTP_USER},****** sftp://${SFTP_HOST}:${SFTP_PORT} -e \"set sftp:auto-confirm yes; mirror --verbose --only-newer --delete ${SFTP_REMOTE_DIR} ${LOCAL_DIR}; quit\""

if ! retry_operation "$SFTP_CMD" "$SFTP_LOG_CMD"; then
    handle_error "SFTP sync failed after multiple attempts"
fi

log "INFO" "SFTP sync completed successfully"

# Check if any files were downloaded
if [ -z "$(ls -A ${LOCAL_DIR})" ]; then
    log "INFO" "No files were downloaded, skipping SMB transfer"
else
    # Check required SMB variables
    check_vars "SMB_HOST" "SMB_SHARE" "SMB_USER" "SMB_PASS"

    # Create dynamic SMB batch file
    SMB_COMMAND="recurse ON;prompt OFF;lcd '${LOCAL_DIR}';mput *;quit"

    # Process the downloaded files
    log "INFO" "Copying files to SMB Share"
    SMB_CMD="smbclient -U ${SMB_USER}%${SMB_PASS} //${SMB_HOST}/${SMB_SHARE} -D '${SMB_PATH}' -c '${SMB_COMMAND}' >> ${LOG_FILE} 2>&1"
    SMB_LOG_CMD="smbclient -U ${SMB_USER}%****** //${SMB_HOST}/${SMB_SHARE} -D '${SMB_PATH}' -c '${SMB_COMMAND}'"

    if ! retry_operation "$SMB_CMD" "$SMB_LOG_CMD"; then
        handle_error "Copy to SMB Share failed after multiple attempts"
    fi

    log "INFO" "Copy to SMB Share completed successfully"
fi

# Clean up the temporary batch files
if [ -f "${SMB_BATCH_FILE}" ]; then
    rm -f ${SMB_BATCH_FILE}
fi

# Calculate and log runtime
end_time=$(date +%s)
runtime=$((end_time - start_time))
log "INFO" "SFTP sync process completed in ${runtime} seconds"
log "INFO" "-------------------------------------"

# Rotate old logs (keep last 7 days)
find ${LOG_DIR} -name "sftp_sync_*.log" -type f -mtime +7 -delete

exit 0
