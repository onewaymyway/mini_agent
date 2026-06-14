---
name: network_error
trigger_event: tool_error
condition:
  error_pattern: "Connection refused|timeout|ETIMEDOUT|ECONNREFUSED|Network unreachable|Name or service not known|SSL.*error|certificate"
inject_as: user
priority: 78
enabled: true
---

**[Reminder] 网络连接错误处理建议：**

1. 检查目标服务是否运行：`curl -v <url>` 或 `ping <host>`
2. 沙箱环境中网络访问可能受限，检查允许的域名列表
3. SSL 证书错误时，确认时间同步是否正确：`date`
4. 若是本地服务，确认端口是否正确监听：`netstat -tlnp | grep <port>` 或 `ss -tlnp`
5. 超时错误考虑增大超时时间参数，或检查网络代理设置
