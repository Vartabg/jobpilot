#!/bin/bash
# JobPilot Chrome Launcher
# Launches Chrome with Remote Debugging enabled so we can connect via CDP

set -e

CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEBUG_PORT=9222

# Check if Chrome is already running with debugging
if curl -s "http://localhost:$DEBUG_PORT/json/version" > /dev/null 2>&1; then
    echo "✓ Chrome is already running with debugging enabled on port $DEBUG_PORT"
    exit 0
fi

# Kill any existing Chrome instances to avoid conflicts (optional - comment out if you want to keep existing)
# pkill -f "Google Chrome" 2>/dev/null || true

echo "🚀 Launching Chrome with Remote Debugging on port $DEBUG_PORT..."
echo "   Using a debug profile based on your Chrome settings."

# Use a separate user data directory for debugging
# This allows CDP to work while keeping a similar browsing experience
DEBUG_PROFILE="$HOME/.jobpilot-chrome-profile"

# Launch Chrome with debugging enabled
"$CHROME_PATH" \
    --remote-debugging-port=$DEBUG_PORT \
    --remote-allow-origins=* \
    --user-data-dir="$DEBUG_PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    "https://www.linkedin.com/jobs/" &

# Wait for Chrome to start
sleep 2

# Verify connection
if curl -s "http://localhost:$DEBUG_PORT/json/version" > /dev/null 2>&1; then
    echo "✓ Chrome is ready! DevTools listening on port $DEBUG_PORT"
    echo ""
    echo "You can now run: python -m jobpilot start"
else
    echo "✗ Failed to connect. Chrome may need a moment to start."
    echo "  Try again in a few seconds, or check if port $DEBUG_PORT is in use."
    exit 1
fi
