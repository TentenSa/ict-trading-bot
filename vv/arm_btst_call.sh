#!/bin/bash
# Arm the 1-Sep 24300 CE BTST watcher at the NEXT session's open (26-Aug).
# Waits for the calendar date to roll first -- "weekday and inside market hours"
# is true today too, so an earlier version of this broke out immediately.
cd /home/nitin/claude/vv
START=$(TZ=Asia/Kolkata date +%Y%m%d)
while :; do
  NOW=$(TZ=Asia/Kolkata date +%Y%m%d)
  h=$(TZ=Asia/Kolkata date +%H%M); d=$(TZ=Asia/Kolkata date +%u)
  if [ "$NOW" != "$START" ] && [ "$d" -le 5 ] && [ "$h" -ge 0914 ] && [ "$h" -lt 1530 ]; then break; fi
  sleep 120
done
echo "[$(TZ=Asia/Kolkata date +'%d-%b %H:%M IST')] arming BTST call watcher (1-Sep 24300 CE, filled 149.50)" >> track_btst.log
nohup python3 -u watch_triggers.py ^NSEI --keep-going \
  --above 24291.00:BREAKEVEN-24291-leg-is-flat-here \
  --above 24313.00:24AUG-HIGH-24313-the-draw-is-being-taken \
  --above 24327.00:TP1-24327-SELL-THE-LIMIT \
  --below 24115.45:SETUP-DEAD-25AUG-LOW-LOST-reversal-void \
  --interval 45 --near 25 --until 10:00 >> track_btst.log 2>&1 &
echo $! > track_btst.pid
