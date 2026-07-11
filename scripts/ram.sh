# Is swapping actively happening right now, or just old parked pages?
vmstat 1 5
# watch the "si" (swap in) and "so" (swap out) columns — near-zero means it's parked, not active

# What's actually using the swap
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
  swap=$(awk '/VmSwap/{print $2}' /proc/$pid/status 2>/dev/null)
  if [ -n "$swap" ] && [ "$swap" != "0" ]; then
    echo "$swap kB - $(cat /proc/$pid/comm 2>/dev/null) (pid $pid)"
  fi
done | sort -rn | head -15

