#!/bin/bash
# Re-arm the BANKNIFTY position watcher at the NEXT session's open.
# Waits for the calendar date to roll first -- an earlier version broke out
# immediately because "weekday and inside market hours" is true today too.
cd /home/nitin/claude/vv
START=$(TZ=Asia/Kolkata date +%Y%m%d)
while :; do
  NOW=$(TZ=Asia/Kolkata date +%Y%m%d)
  h=$(TZ=Asia/Kolkata date +%H%M); d=$(TZ=Asia/Kolkata date +%u)
  if [ "$NOW" != "$START" ] && [ "$d" -le 5 ] && [ "$h" -ge 0914 ] && [ "$h" -lt 1530 ]; then break; fi
  sleep 120
done
echo "[$(TZ=Asia/Kolkata date +'%d-%b %H:%M IST')] re-arming BNF position watcher for the new session" >> track_bnf.log
nohup python3 -u watch_triggers.py ^NSEBANK --keep-going \
  --above 57430.00:BNF-STOP-HIT-EXIT-NOW \
  --above 57431.80:BNF-THESIS-DEAD-20AUG-LOW-RECLAIMED \
  --below 57217.60:BNF-WAYPOINT-57217-DO-NOT-BOOK-HERE \
  --below 57001.75:BNF-TARGET-57001-TAKE-IT \
  --interval 45 --near 40 --until 10:00 >> track_bnf.log 2>&1 &
echo $! > track_bnf.pid
