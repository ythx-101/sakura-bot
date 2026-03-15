#!/bin/bash
# jvav 自动追新版 + bot 重启
# 用法: crontab 或 systemd timer 定期执行

LOG="/var/log/jav-update.log"
CURRENT=$(pip3 show jvav 2>/dev/null | grep Version | awk '{print $2}')
LATEST=$(pip3 index versions jvav 2>/dev/null | grep LATEST | awk '{print $2}')

if [ -z "$LATEST" ]; then
    echo "$(date) [SKIP] 无法获取最新版本" >> "$LOG"
    exit 0
fi

if [ "$CURRENT" = "$LATEST" ]; then
    echo "$(date) [OK] jvav $CURRENT 已是最新" >> "$LOG"
    exit 0
fi

echo "$(date) [UPDATE] jvav $CURRENT → $LATEST" >> "$LOG"
pip3 install "jvav==$LATEST" >> "$LOG" 2>&1

if [ $? -eq 0 ]; then
    echo "$(date) [RESTART] 重启 bot..." >> "$LOG"
    systemctl restart tg-search-bot
    echo "$(date) [DONE] 升级完成" >> "$LOG"
else
    echo "$(date) [FAIL] 升级失败" >> "$LOG"
fi
