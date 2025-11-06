#!/bin/bash

# Updated: 2025-10-26 - Added postgres monitoring, environment variables support
# Working Directory: /home/docker
# Environment: Uses .env file for all secrets (no hard-coded values)

CONTAINER_NAMES=("ai-voice-connector-engine" "ai-voice-connector-opensips" "postgres" "server")

RAM_THRESHOLD=80.0
CPU_THRESHOLD=97.0
LOW_CPU_THRESHOLD=3.0      # below this = considered idle
LOW_CPU_DURATION=1800       # 30 minutes in seconds
CHECK_INTERVAL=5            # interval between checks

echo "🚀 Starting Docker resource monitor..."
echo "Monitoring containers: ${CONTAINER_NAMES[*]}"
echo "RAM Threshold: ${RAM_THRESHOLD}% | CPU Threshold: ${CPU_THRESHOLD}%"
echo "Low-CPU Restart: if CPU < ${LOW_CPU_THRESHOLD}% for ${LOW_CPU_DURATION}s"
echo "Soniox 429/402 errors will restart ALL containers (only once per new error)"
echo "Working Dir: $(pwd)"
echo "-----------------------------------------------------"

declare -A breach_count
declare -A low_cpu_start

# Track last restart caused by Soniox error
last_soniox_restart=0

while true; do
  for container in "${CONTAINER_NAMES[@]}"; do
    if [ ! "$(docker ps -q -f name=$container)" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - INFO: Container '$container' is not running. Skipping."
        continue
    fi

    stats=$(docker stats --no-stream --format "{{.CPUPerc}}|{{.MemPerc}}" "$container")

    cpu=$(echo "$stats" | cut -d'|' -f1 | tr -d '%')
    ram=$(echo "$stats" | cut -d'|' -f2 | tr -d '%')
    cpu=${cpu/","/"."}   # fix decimal commas
    ram=${ram/","/"."}

    echo "$(date '+%Y-%m-%d %H:%M:%S') - '$container' CPU: ${cpu}% | RAM: ${ram}%"

    # --- HIGH RESOURCE CHECK ---
    if (( $(echo "$cpu > $CPU_THRESHOLD || $ram > $RAM_THRESHOLD" | bc -l) )); then
      breach_count["$container"]=$(( ${breach_count["$container"]} + 1 ))
      if [ "${breach_count["$container"]}" -ge 3 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - RESTART: High usage on '$container'"
        docker compose restart
        breach_count["$container"]=0
        low_cpu_start["$container"]=0
      fi
    else
      breach_count["$container"]=0
    fi

    # --- LOW CPU RESTART CHECK ---
    if (( $(echo "$cpu < $LOW_CPU_THRESHOLD" | bc -l) )); then
      if [ -z "${low_cpu_start["$container"]}" ] || [ "${low_cpu_start["$container"]}" -eq 0 ]; then
        low_cpu_start["$container"]=$(date +%s)
      else
        now=$(date +%s)
        idle_time=$(( now - low_cpu_start["$container"] ))
        if [ "$idle_time" -ge "$LOW_CPU_DURATION" ]; then
          echo "$(date '+%Y-%m-%d %H:%M:%S') - RESTART: '$container' idle for ${LOW_CPU_DURATION}s (<$LOW_CPU_THRESHOLD% CPU)"
          docker compose restart 
          low_cpu_start["$container"]=0
        fi
      fi
    else
      low_cpu_start["$container"]=0
    fi

    # --- SONIOX ERROR CHECK (429 rate limit, 402 balance exhausted) ---
    if [ "$container" == "ai-voice-connector-engine" ]; then
      since_time=$(date -d @"$last_soniox_restart" +"%Y-%m-%dT%H:%M:%S")
      new_logs=$(docker logs --since "$since_time" "$container" 2>&1)

      if echo "$new_logs" | grep -qE "Soniox error (429|402)"; then
        error_type=$(echo "$new_logs" | grep -oE "Soniox error (429|402)" | tail -1)
        echo "$(date '+%Y-%m-%d %H:%M:%S') - CRITICAL: New $error_type detected! Restarting ALL containers..."
        docker compose restart

        # Reset timers after global restart
        for c in "${CONTAINER_NAMES[@]}"; do
          breach_count["$c"]=0
          low_cpu_start["$c"]=0
        done

        # Record this restart time
        last_soniox_restart=$(date +%s)
      fi
    fi

  done
  sleep "$CHECK_INTERVAL"
done
