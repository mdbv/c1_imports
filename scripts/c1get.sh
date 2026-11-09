#!/usr/bin/env bash

# Variables from environment variables with defaults
LOG_DIR="${LOG_DIR:-/var/log}"
LOCAL_DIR="${LOCAL_DIR:-/app/files}"
STAGING_DIR="${STAGING_DIR:-/app/state/sftp-staging}"
LOCK_FILE="${LOCK_FILE:-/var/lock/c1get.lock}"
LATEST_LOG_LINK="${LOG_DIR}/sftp_sync_latest.log"

mkdir -p "${LOG_DIR}" "${LOCAL_DIR}" "${STAGING_DIR}" "$(dirname "${LOCK_FILE}")"

# One sync at a time. Cron and the web "Run sync" button share this lock.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    message="[${timestamp}] [INFO] Sync already in progress, exiting"
    echo "${message}"
    if [ -e "${LATEST_LOG_LINK}" ]; then
        echo "${message}" >> "${LATEST_LOG_LINK}"
    fi
    exit 0
fi
echo $$ > "${LOCK_FILE}"

# Timestamped log file
TIMESTAMP=$(date -u '+%Y%m%d_%H%M%S')
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
    local timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
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

# Mirror keeps the remote names. Property/ertv1109.dat and Utilities/ertv1109.dat
# would be indistinguishable once staff download them, so publish a copy whose
# name starts with the folder it came from. Staging stays a faithful mirror so
# --delete does not treat those renamed copies as extras.
publish_files() {
    local staging="${1%/}"
    local dest_root="${2%/}"
    local manifest src rel dir base folder target_rel target existing existing_rel

    manifest=$(mktemp)
    trap 'rm -f "$manifest"' RETURN
    mkdir -p "$dest_root"

    while IFS= read -r -d '' src; do
        rel="${src#"$staging"/}"
        dir=$(dirname "$rel")
        base=$(basename "$rel")
        target_rel="$rel"
        if [ "$dir" != "." ] && [[ "$base" =~ ^ertv[0-9]{4}\.(dat|rpt)$ ]]; then
            folder=$(basename "$dir")
            target_rel="${dir}/${folder}-${base}"
        fi
        target="${dest_root}/${target_rel}"
        mkdir -p "$(dirname "$target")"
        if [ ! -f "$target" ] || [ "$src" -nt "$target" ] || [ "$(stat -c %s "$src")" != "$(stat -c %s "$target")" ]; then
            cp -p "$src" "$target"
            if [ "$target_rel" != "$rel" ]; then
                log "INFO" "Renamed ${rel} -> ${target_rel}"
            fi
        fi
        printf '%s\0' "$target_rel" >> "$manifest"
    done < <(find "$staging" -type f -print0)

    while IFS= read -r -d '' existing; do
        existing_rel="${existing#"$dest_root"/}"
        if ! grep -F -z -x -q -- "$existing_rel" "$manifest"; then
            rm -f "$existing"
            log "INFO" "Removed ${existing_rel}"
        fi
    done < <(find "$dest_root" -type f -print0)

    find "$dest_root" -mindepth 1 -type d -empty -delete
}

# Check required SFTP variables
check_vars "SFTP_HOST" "SFTP_USER" "SFTP_PASS"

# Log start of sync
log "INFO" "Starting SFTP sync from ${SFTP_HOST}:${SFTP_REMOTE_DIR}"

# Execute the SFTP command with retry
SFTP_CMD="lftp -u ${SFTP_USER},${SFTP_PASS} sftp://${SFTP_HOST}:${SFTP_PORT} -e \"set sftp:auto-confirm yes; mirror --verbose --only-newer --delete ${SFTP_REMOTE_DIR} ${STAGING_DIR}; quit\" >> ${LOG_FILE} 2>&1"
SFTP_LOG_CMD="lftp -u ${SFTP_USER},****** sftp://${SFTP_HOST}:${SFTP_PORT} -e \"set sftp:auto-confirm yes; mirror --verbose --only-newer --delete ${SFTP_REMOTE_DIR} ${STAGING_DIR}; quit\""

if ! retry_operation "$SFTP_CMD" "$SFTP_LOG_CMD"; then
    handle_error "SFTP sync failed after multiple attempts"
fi

log "INFO" "SFTP sync completed successfully"
publish_files "${STAGING_DIR}" "${LOCAL_DIR}"
log "INFO" "Published files to ${LOCAL_DIR}"

# Calculate and log runtime
end_time=$(date +%s)
runtime=$((end_time - start_time))
log "INFO" "SFTP sync process completed in ${runtime} seconds"
log "INFO" "-------------------------------------"

# Rotate old logs (keep last 7 days)
find "${LOG_DIR}" -name "sftp_sync_*.log" -type f -mtime +7 -delete

exit 0
