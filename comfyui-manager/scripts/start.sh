#!/bin/bash
# ====================================
#   ComfyUI Manager - Start Server
# ====================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$APP_DIR/logs/server.pid"
LOG_FILE="$APP_DIR/logs/server.log"

cd "$APP_DIR"

# Default configuration
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8730}"
WORKERS="${WORKERS:-1}"
RELOAD="${RELOAD:-false}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --reload)
            RELOAD="true"
            shift
            ;;
        --daemon|-d)
            DAEMON="true"
            shift
            ;;
        --help|-h)
            echo "Usage: start.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --host HOST      Host to bind to (default: 0.0.0.0)"
            echo "  --port PORT      Port to bind to (default: 8730)"
            echo "  --workers N      Number of workers (default: 1)"
            echo "  --reload         Enable auto-reload for development"
            echo "  --daemon, -d     Run in background"
            echo "  --help, -h       Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  Server is already running (PID: $OLD_PID)"
        echo "   Use ./scripts/stop.sh to stop it first"
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "⚠️  Virtual environment not found. Run ./scripts/setup.sh first"
    exit 1
fi

# Build uvicorn command
UVICORN_CMD="uvicorn app:app --host $HOST --port $PORT"

if [ "$WORKERS" -gt 1 ]; then
    UVICORN_CMD="$UVICORN_CMD --workers $WORKERS"
fi

if [ "$RELOAD" = "true" ]; then
    UVICORN_CMD="$UVICORN_CMD --reload"
fi

echo "===================================="
echo "  ComfyUI Manager - Starting Server"
echo "===================================="
echo ""
echo "🌐 Host: $HOST"
echo "🔌 Port: $PORT"
echo "👷 Workers: $WORKERS"
echo "🔄 Auto-reload: $RELOAD"
echo ""

# Create logs directory
mkdir -p logs

if [ "$DAEMON" = "true" ]; then
    echo "🚀 Starting server in background..."
    nohup $UVICORN_CMD > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2
    
    if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "✅ Server started (PID: $(cat "$PID_FILE"))"
        echo "   Logs: $LOG_FILE"
        echo "   Access: http://$HOST:$PORT"
    else
        echo "❌ Failed to start server. Check logs:"
        tail -20 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
else
    echo "🚀 Starting server in foreground..."
    echo "   Press Ctrl+C to stop"
    echo ""
    exec $UVICORN_CMD
fi
