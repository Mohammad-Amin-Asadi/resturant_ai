#!/bin/bash
# Real-time call monitoring script for Restaurant Ordering System

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║          🍽️  RESTAURANT CALL MONITOR (Bozorgmehr) 🍽️                ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Watching for incoming calls and transcripts..."
echo "Press Ctrl+C to stop"
echo ""

# Run both logs in parallel with color coding
docker logs -f ai-voice-connector-opensips 2>&1 | sed 's/^/[OpenSIPS] /' &
OPENSIPS_PID=$!

docker logs -f ai-voice-connector-engine 2>&1 | sed 's/^/[Engine]   /' &
ENGINE_PID=$!

# Wait for both
wait $OPENSIPS_PID $ENGINE_PID

