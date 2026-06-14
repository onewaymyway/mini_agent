---
name: disk_space_full
trigger_event: tool_error
condition:
  error_pattern: "No space left on device|ENOSPC|disk.*full|quota.*exceeded"
inject_as: user
priority: 90
enabled: true
---

**[Reminder] 磁盘空间不足处理建议：**

1. 立即检查磁盘使用情况：`df -h`
2. 找出大文件/目录：`du -sh /* 2>/dev/null | sort -hr | head -20`
3. 清理临时文件：`rm -rf /tmp/* 2>/dev/null`
4. 清理 pip 缓存：`pip cache purge`
5. 清理 apt 缓存：`apt-get clean`
6. 检查日志文件大小：`du -sh /var/log/*`

**注意**：磁盘空间不足会导致文件写入损坏，优先清理再继续操作。
