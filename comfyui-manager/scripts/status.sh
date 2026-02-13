#!/bin/bash
# ====================================
#   ComfyUI Manager - Server Status
# ====================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$APP_DIR/logs/server.pid"
LOG_FILE="$APP_DIR/logs/server.log"

echo "===================================="
echo "  ComfyUI Manager - Server Status"
echo "===================================="
echo ""

# Check PID file
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "✅ Server is RUNNING (PID: $PID)"
        
        # Get process info
        echo ""
        echo "Process info:"
        ps -p "$PID" -o pid,ppid,%cpu,%mem,etime,cmd --no-headers 2>/dev/null | head -1
        
        # Get port info
        PORT=$(ss -tlnp 2>/dev/null | grep "$PID" | awk '{print $4}' | grep -oP ':\K\d+$' | head -1)
        if [ -n "$PORT" ]; then
            echo ""
            echo "🌐 Listening on port: $PORT"
        fi
    else
        echo "⚠️  Server is NOT RUNNING (stale PID file)"
        echo "   PID $PID is not active"
    fi
else
    echo "⚠️  Server is NOT RUNNING (no PID file)"
    
    # Check for any uvicorn processes
    PIDS=$(pgrep -f "uvicorn app:app" 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo ""
        echo "   Found untracked uvicorn processes: $PIDS"
    fi
fi

# Show recent logs if available
if [ -f "$LOG_FILE" ]; then
    echo ""
    echo "📄 Recent logs (last 10 lines):"
    echo "------------------------------------"
    tail -10 "$LOG_FILE"
fi
