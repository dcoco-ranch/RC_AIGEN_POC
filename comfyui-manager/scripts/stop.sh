#!/bin/bash
# ====================================
#   ComfyUI Manager - Stop Server
# ====================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$APP_DIR/logs/server.pid"

echo "===================================="
echo "  ComfyUI Manager - Stop Server"
echo "===================================="
echo ""

if [ ! -f "$PID_FILE" ]; then
    echo "⚠️  No PID file found. Server may not be running."
    
    # Try to find and kill any running uvicorn processes for this app
    PIDS=$(pgrep -f "uvicorn app:app" 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "   Found running uvicorn processes: $PIDS"
        read -p "   Kill these processes? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            kill $PIDS 2>/dev/null
            echo "✅ Processes killed"
        fi
    fi
    exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "🛑 Stopping server (PID: $PID)..."
    kill "$PID"
    
    # Wait for graceful shutdown
    for i in {1..10}; do
        if ! kill -0 "$PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    
    # Force kill if still running
    if kill -0 "$PID" 2>/dev/null; then
        echo "⚠️  Forcing shutdown..."
        kill -9 "$PID" 2>/dev/null
    fi
    
    rm -f "$PID_FILE"
    echo "✅ Server stopped"
else
    echo "⚠️  Server not running (stale PID file)"
    rm -f "$PID_FILE"
fi
