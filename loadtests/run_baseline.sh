#!/bin/bash
# Run baseline load tests to establish performance metrics.
#
# Prerequisites:
#   - Server must be running: uv run uvicorn transcription_service.main:app --host 0.0.0.0 --port 8000
#
# Usage:
#   ./run_baseline.sh              # Run with default settings
#   ./run_baseline.sh 200 20 120   # 200 users, 20/sec spawn rate, 120 seconds

set -e

HOST="${HOST:-http://localhost:8000}"
USERS="${1:-100}"
SPAWN_RATE="${2:-10}"
DURATION="${3:-60}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$RESULTS_DIR"

echo "========================================"
echo "  Transcription Service Load Test"
echo "========================================"
echo ""
echo "Configuration:"
echo "  Host:       $HOST"
echo "  Users:      $USERS"
echo "  Spawn rate: $SPAWN_RATE/sec"
echo "  Duration:   ${DURATION}s"
echo ""
echo "Output: $RESULTS_DIR/baseline_$TIMESTAMP"
echo ""

# Check if server is running
echo "Checking server health..."
if ! curl -sf "$HOST/v1/health" > /dev/null 2>&1; then
    echo "ERROR: Server not responding at $HOST/v1/health"
    echo "Start the server with: uv run uvicorn transcription_service.main:app --host 0.0.0.0 --port 8000"
    exit 1
fi
echo "Server is healthy!"
echo ""

# Run the load test
echo "Starting load test..."
cd "$SCRIPT_DIR"

locust \
    --host="$HOST" \
    --headless \
    --users "$USERS" \
    --spawn-rate "$SPAWN_RATE" \
    --run-time "${DURATION}s" \
    --csv="$RESULTS_DIR/baseline_$TIMESTAMP" \
    --html="$RESULTS_DIR/baseline_${TIMESTAMP}.html" \
    --only-summary

echo ""
echo "========================================"
echo "  Results saved to:"
echo "  - CSV:  $RESULTS_DIR/baseline_${TIMESTAMP}_stats.csv"
echo "  - HTML: $RESULTS_DIR/baseline_${TIMESTAMP}.html"
echo "========================================"
