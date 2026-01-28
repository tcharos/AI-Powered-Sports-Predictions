#!/bin/bash

# Change directory to project root
cd "$(dirname "$0")/.." || exit

APP_PATH="web_ui/app.py"
LOG_FILE="logs/ui.log"
VENV_PYTHON="venv/bin/python"

function get_pid {
    ps aux | grep "$APP_PATH" | grep -v grep | awk '{print $2}'
}

function start {
    PID=$(get_pid)
    if [ -n "$PID" ]; then
        echo "Server is already running (PID: $PID)."
    else
        echo "Starting server..."
        nohup $VENV_PYTHON $APP_PATH > $LOG_FILE 2>&1 &
        sleep 2
        NEW_PID=$(get_pid)
        echo "Server started (PID: $NEW_PID). Logs at $LOG_FILE"
    fi
}

function stop {
    PID=$(get_pid)
    if [ -n "$PID" ]; then
        echo "Stopping server (PID: $PID)..."
        kill -9 $PID
        echo "Server stopped."
    else
        echo "Server is not running."
    fi
}

function restart {
    echo "Restarting server..."
    stop
    sleep 2
    start
}

function status {
    PID=$(get_pid)
    if [ -n "$PID" ]; then
        echo "Server is RUNNING (PID: $PID)."
    else
        echo "Server is STOPPED."
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
exit 0
