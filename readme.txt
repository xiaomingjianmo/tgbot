Render 部署说明（Telegram 群关键词杀广机器人）
================================================

一、创建 Web Service
-------------------
1) 登录 https://render.com → New → Web Service
2) 选择方式：
   - 若使用 Git：把本目录提交到 GitHub 后选择仓库；
   - 或者 Manual Deploy：上传本 zip 包的内容。
3) Environment 选：Python 3

二、环境变量（Settings → Environment）
------------------------------------
添加：
- BOT_TOKEN = 你的 Telegram 机器人 Token
可选：
- WARN_THRESHOLD = 2
- MUTE_SECONDS = 3600
- DELETE_NOTICE = true

三、启动命令
------------
Render 会自动检测 `Procfile`：
web: python main.py

四、Telegram 设置
-----------------
1) 把机器人加到目标群 → 提升为管理员（删除消息、限制成员权限）。
2) 到 @BotFather 关闭隐私模式：
   /setprivacy → 选择你的机器人 → Disable
3) 群里用管理员账号配置关键词：
   /addkw 返利 秒出 官方客服
   /addkw /\b免费(领取|送)/ /https?:\/\/t\.cn/
   /listkw  查看
   /rmkw ... 删除
   /clearkw 清空

五、健康检查
------------
- Render 会访问根路径 `/`，返回 200 即健康。
- 也可访问 `/ping` 测试。

六、常见问题
------------
- 没删消息：检查是否管理员、隐私模式是否关闭、关键词是否命中。
- 不禁言：达到 WARN_THRESHOLD+1 次才处罚；并需“限制成员”权限。
- 需要持久化关键词：可改为 SQLite（如需我可以提供持久化版本）。