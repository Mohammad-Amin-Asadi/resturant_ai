#!/bin/bash

# Restaurant Call Monitor - Watch for incoming calls in real-time

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     🍽️  RESTAURANT CALL MONITOR - Waiting for calls...     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Server: 185.110.188.112:5060"
echo "Monitoring: SIP Port 5060 + OpenSIPS logs + Engine logs"
echo ""
echo "What you should see when a call arrives:"
echo "  1. SIP INVITE packet (below)"
echo "  2. OpenSIPS: 'Initial INVITE - starting B2B session'"
echo "  3. Engine: 'CALL ACCEPTED' with caller details"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "📞 CALL NOW and watch below:"
echo ""

# Function to monitor SIP packets
monitor_sip() {
    sudo timeout 300 tcpdump -i any -n -l port 5060 2>/dev/null | while read line; do
        if echo "$line" | grep -q "INVITE"; then
            echo "📞 INCOMING CALL DETECTED!"
            echo "   $line"
            echo ""
        elif echo "$line" | grep -q "SIP/2.0 200"; then
            echo "✅ Call Answered: $line"
        elif echo "$line" | grep -q "BYE"; then
            echo "👋 Call Ended: $line"
            echo ""
        fi
    done
}

# Function to monitor OpenSIPS logs
monitor_opensips() {
    sudo docker logs -f ai-voice-connector-opensips 2>&1 | grep --line-buffered -E "INVITE|B2B|Method:|From:|To:" | while read line; do
        echo "🔵 OpenSIPS: $line"
    done
}

# Function to monitor Engine logs
monitor_engine() {
    sudo docker logs -f ai-voice-connector-engine 2>&1 | grep --line-buffered -E "CALL ACCEPTED|CALL REJECTED|Caller:|STT|وضعیت" | while read line; do
        echo "🤖 Engine: $line"
    done
}

# Run all monitors in parallel
monitor_sip &
SIP_PID=$!

monitor_opensips &
OPENSIPS_PID=$!

monitor_engine &
ENGINE_PID=$!

# Wait for user interrupt
trap "kill $SIP_PID $OPENSIPS_PID $ENGINE_PID 2>/dev/null; echo 'Monitor stopped.'; exit" SIGINT SIGTERM

echo "Monitoring... Press Ctrl+C to stop"
echo ""

wait

