# Is swapping actively happening right now, or just old parked pages?
# vmstat 1 5
vmstat -S M 1 5
# watch the "si" (swap in) and "so" (swap out) columns — near-zero means it's parked, not active

# What's actually using the swap
#for pid in $(ls /proc | grep -E '^[0-9]+$'); do
#  swap=$(awk '/VmSwap/{print $2}' /proc/$pid/status 2>/dev/null)
#  if [ -n "$swap" ] && [ "$swap" != "0" ]; then
#    echo "$swap kB - $(cat /proc/$pid/comm 2>/dev/null) (pid $pid)"
#  fi
#done | sort -rn | head -15

for pid in $(ls /proc | grep -E '^[0-9]+$'); do
  swap_kb=$(awk '/VmSwap/{print $2}' /proc/$pid/status 2>/dev/null)
  if [ -n "$swap_kb" ] && [ "$swap_kb" != "0" ]; then
    awk -v kb="$swap_kb" -v pid="$pid" -v comm="$(cat /proc/$pid/comm 2>/dev/null)" \
      'BEGIN{printf "%.1f MB - %s (pid %s)\n", kb/1024, comm, pid}'
  fi
done | sort -rn | head -50

for pid in $(pgrep -f llama); do
  echo "pid $pid: $(cat /proc/$pid/comm)"
  awk '/VmRSS|VmSwap|VmSize/' /proc/$pid/status
done