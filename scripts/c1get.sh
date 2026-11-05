#!/usr/bin/env bash

# Variables from environment variables with defaults
LOG_DIR="${LOG_DIR:-/var/log}"
LOCAL_DIR="${LOCAL_DIR:-/app/files}"
LOCK_FILE="${LOCK_FILE:-/var/lock/c1get.lock}"
LATEST_LOG_LINK="${LOG_DIR}/sftp_sync_latest.log"

mkdir -p "${LOG_DIR}" "${LOCAL_DIR}" "$(dirname "${LOCK_FILE}")"

# One sync at a time. Cron and the web "Run sync" button share this lock.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    message="[${timestamp}] [INFO] Sync already in progress, exiting"
    echo "${message}"
    if [ -e "${LATEST_LOG_LINK}" ]; then
        echo "${message}" >> "${LATEST_LOG_LINK}"
    fi
    exit 0
fi
echo $$ > "${LOCK_FILE}"

# Timestamped log file
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="${LOG_DIR}/sftp_sync_${TIMESTAMP}.log"
ln -sf "${LOG_FILE}" "${LATEST_LOG_LINK}"

# SFTP connection details
SFTP_HOST="${SFTP_HOST}"
SFTP_PORT="${SFTP_PORT:-22}"
SFTP_USER="${SFTP_USER}"
SFTP_PASS="${SFTP_PASS}"
SFTP_REMOTE_DIR="${SFTP_REMOTE_DIR:-.}"

# Track performance
start_time=$(date +%s)

# Enhanced logging
log() {
    local level=$1
    local message=$2
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${level}] ${message}" >> "${LOG_FILE}"
}

# Function to handle errors with alerting
handle_error() {
    local error_message=$1
    log "ERROR" "$error_message"
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

# Check required SFTP variables
check_vars "SFTP_HOST" "SFTP_USER" "SFTP_PASS"

# Log start of sync
log "INFO" "Starting SFTP sync from ${SFTP_HOST}:${SFTP_REMOTE_DIR}"

# Execute the SFTP command with retry
SFTP_CMD="lftp -u ${SFTP_USER},${SFTP_PASS} sftp://${SFTP_HOST}:${SFTP_PORT} -e \"set sftp:auto-confirm yes; mirror --verbose --only-newer --delete ${SFTP_REMOTE_DIR} ${LOCAL_DIR}; quit\" >> ${LOG_FILE} 2>&1"
SFTP_LOG_CMD="lftp -u ${SFTP_USER},****** sftp://${SFTP_HOST}:${SFTP_PORT} -e \"set sftp:auto-confirm yes; mirror --verbose --only-newer --delete ${SFTP_REMOTE_DIR} ${LOCAL_DIR}; quit\""

if ! retry_operation "$SFTP_CMD" "$SFTP_LOG_CMD"; then
    handle_error "SFTP sync failed after multiple attempts"
fi

log "INFO" "SFTP sync completed successfully"

# Calculate and log runtime
end_time=$(date +%s)
runtime=$((end_time - start_time))
log "INFO" "SFTP sync process completed in ${runtime} seconds"
log "INFO" "-------------------------------------"

# Rotate old logs (keep last 7 days)
find "${LOG_DIR}" -name "sftp_sync_*.log" -type f -mtime +7 -delete

exit 0
