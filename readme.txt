# Telegram 反广告机器人（SQLite 持久化 + 关键词 + Gemini AI）

一个面向 **Telegram 群** 的“杀广”机器人：  
- 本地 **SQLite 持久化**（关键词/警告/AI 开关与阈值）  
- **关键词快速过滤** + **Gemini 大模型 AI 智能识别**（可自定义阈值）  
- 支持 **导入/导出** 关键词、导出 **AI 判定样本**  
- 纯 Python，**零服务器依赖**（long polling），Windows / Linux 通用

> ✅ 适合个人/中小群免费部署；不需要域名与证书（Webhook 可选）。  
> ✅ 默认兼容 **Gemini 2.0 Flash**；可切换到 `gemini-2.0-pro` 获得更强识别。

---

## ✨ 功能亮点

- **SQLite 持久化**：重启不丢关键词/配置/警告计数  
- **双引擎过滤**：关键词先挡，AI 复核，降低误判与漏判  
- **群内命令管理**：管理员可直接在群里增删关键词、开关 AI、调阈值  
- **样本采集**：自动记录 AI 判定样本，管理员一键导出 JSON 复盘  
- **多群多配置**：每个群独立保存关键词与 AI 设置  
- **部署简单**：Windows 一键后台 `pythonw`，Linux systemd 自启动

---

## 📦 目录结构（示例）

├── main.py # 机器人主程序
├── requirements.txt # 依赖
├── README.md # 使用说明（本文件）
├── .env.example # 环境变量示例（可复制为 .env）
├── tgbot.service # Linux systemd 单元示例（可选）
└── Procfile # Render/Heroku 等平台运行声明（可选）


---

## 🔧 环境要求

- Python 3.9+（推荐 3.10/3.11/3.12/3.13）
- 网络可访问 Telegram API 与 Google Generative AI（如果启用 AI）

安装依赖：
```bash
pip install -r requirements.txt

▶️ 运行
Windows（前台/后台）

前台测试：

python .\main.py


后台无窗口：

pythonw .\main.py


健康检查：

Invoke-WebRequest http://127.0.0.1:10000/ping
# 返回 pong 表示进程在运行


建议用“任务计划程序”或 NSSM 安装为服务，开机自启更稳。
Linux（systemd 自启动）

安装 Python 与依赖：

sudo apt/yum/dnf install -y python3 python3-pip
pip3 install -r requirements.txt


创建环境文件（例如 /etc/sysconfig/tgbot）：

sudo tee /etc/sysconfig/tgbot >/dev/null <<'EOF'
BOT_TOKEN=123456789:xxxx
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash
AI_ON=true
AI_SCORE=0.70
WARN_THRESHOLD=2
MUTE_SECONDS=3600
DELETE_NOTICE=true
EOF
sudo chmod 600 /etc/sysconfig/tgbot


放置 tgbot.service（见本仓库示例）：

sudo cp tgbot.service /etc/systemd/system/tgbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now tgbot
sudo systemctl status tgbot -l


查看运行日志：

journalctl -u tgbot -f


注：本项目为 long polling，无需对外开放入站端口。

🤖 群内命令（管理员）

添加关键词：/addkw 返利 秒出 /https?:\/\/t\.me\//

删除关键词：/rmkw 返利 /https?:\/\/t\.me\//

清空关键词：/clearkw

查看关键词：/listkw

开/关 AI：/aion、/aioff

调整 AI 阈值：/aiscore 0.70

清空警告计数：/resetwarns

导出关键词：/exportkw

导入关键词：

/importkw {"keywords":["返利","/秒出.*/","官方客服"]}


导出 AI 样本（最近）：/aiexport（会发一个 JSON 文件）

注意：

机器人必须是管理员，且勾选“删除消息/限制成员”等权限。

群最好是超级群（否则禁言/踢人可能报错）。

需要在 @BotFather 关闭隐私模式：/setprivacy → 选择 bot → Disable。

🧠 关于 Gemini 模型

2025 年后 gemini-1.5-flash 已下线，请使用：

gemini-2.0-flash（默认，速度快）

或 gemini-2.0-pro（更准）

修改方式：

环境变量：setx GEMINI_MODEL "gemini-2.0-flash"

或直接改代码：GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

🧪 调试与排错

查看运行日志（Windows）：Get-Content .\bot.log -Wait

AI 没反应：

/aion 是否开启；2) GEMINI_API_KEY 是否设置；

网络是否可连 Google；4) GEMINI_MODEL 是否存在

提示 404 models/... not found：模型名过期，改为 gemini-2.0-flash 或 gemini-2.0-pro

ConnectionResetError 10054：Telegram 偶尔断线，long polling 会自动重连

不删消息：检查机器人权限 & 群是否为超级群；/listkw 查看关键词是否生效
如果你要让 main.py 自动读取 .env，可自行加入 python-dotenv 并在代码顶部添加：

from dotenv import load_dotenv; load_dotenv()

📦 requirements.txt
pyTelegramBotAPI>=4.15.4
Flask>=3.0.0
google-generativeai>=0.7.0


如使用 .env：再加 python-dotenv>=1.0.1

⚙️ tgbot.service（Linux 可选）
[Unit]
Description=Telegram AntiSpam Bot (SQLite + Gemini)
After=network.target

[Service]
Type=simple
User=youruser
Group=youruser
WorkingDirectory=/home/youruser/telegram_bot
EnvironmentFile=/etc/sysconfig/tgbot
ExecStart=/usr/bin/python3 /home/youruser/telegram_bot/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target


将 youruser 与路径按实际修改；环境变量放在 /etc/sysconfig/tgbot。

📄 Procfile（Render/Heroku 类平台可选）
web: python main.py


这些平台一般会提供端口（PORT 环境变量），本项目已兼容；
由于我们是 polling，不需要对外流量，web 进程仅用于健康检查。
