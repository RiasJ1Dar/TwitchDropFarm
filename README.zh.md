[Українська](README.md) · [English](README.en.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · **简体中文**

# TwitchDropFarm

在 Twitch 上刷 **timed drops**，无需打开浏览器，屏幕上也不必挂着直播。程序自己读取库存，
判断什么值得刷，找到合适的频道并向 Twitch 投递观看时长；领到的奖励会显示在窗口、系统托盘
和 Telegram 里。

只有一个 `.exe`，旁边不带任何运行时：没有 Node.js，没有 Playwright，也不捆绑浏览器。登录
时使用系统里已经装好的浏览器（Edge 或 Chrome）。

> **关于语言。** 界面、日志和源码注释均为乌克兰语。本文档中出现的一切——文件名、配置键、
> 命令——都与程序完全一致。

## 功能

- **来不及时提前告知**：如果剩余时间少于还需要的观看分钟数，它会提前说明。
- **自己做选择。** 四种模式：按优先级列表、按最近截止时间、按最紧凑匹配（尽量多完成几个
  活动），或者只做账号已绑定、且发放真实道具的活动。
- **通过 PubSub 同时盯住多达 198 个频道**，直播一停就切换。
- **自动领取掉落**，随即转向下一个。
- **窗口**分四个标签页：挖矿、频道、库存、设置。
- **系统托盘**：最小化、通知、后台启动。
- **Telegram 机器人**：状态、库存、活动、暂停/继续、切换频道、管理优先级、完全重启——用
  按钮或命令都行。
- **扛得住故障**：断网、DNS 消失、电脑睡眠、Twitch 的临时错误。万不得已时会自行重启。
- **发现停滞**：如果分钟数不再增加（例如同一个账号正在别处手动看 Twitch），它会讲出来，
  而不是闷声不响。

## 环境要求

- Windows 10/11
- Python 3.10+ —— 仅用于从源码运行或构建 `.exe`
- Edge 或 Chrome —— 仅用于首次登录

## 运行

从源码运行：

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

构建好的 `.exe`：

```bash
dist\TwitchDropFarm.exe
```

首次启动时，程序会打开一个带确认码的 Twitch 页面。登录之后令牌会被保存，不再询问。

### 命令行参数

| 参数 | 作用 |
|---|---|
| `--console` | 无窗口，只用控制台——适合服务器或开机自启 |
| `--tray` | 启动时最小化到托盘 |
| `--log` | 写入 `log.txt` |
| `-v`、`-vv`、`-vvv` | 日志更详细（可重复） |
| `--auth-only` | 仅完成认证后退出 |
| `--dump-inventory` | 打印全部活动与掉落后退出 |
| `--test-telegram` | 发送一条测试消息后退出 |
| `--version` | 版本 |

## 配置

`settings.json` 位于状态目录（见下文），首次启动时自动创建。示例：
[`settings.example.json`](settings.example.json)。

| 键 | 含义 |
|---|---|
| `farm_mode` | `0` —— 优先级列表，`1` —— 最近截止，`2` —— 最紧凑匹配，`3` —— 仅已绑定的活动 |
| `priority` | 按偏好排序的游戏列表 |
| `exclude` | 不去碰的游戏 |
| `farm_cosmetics` | 接受只发徽章和表情的活动 |
| `verify_channel_drops` | 逐个频道核实掉落是否真的开启（更慢，更可靠） |
| `start_in_tray` | 启动时最小化 |
| `tray_notifications` | 弹出通知 |
| `dark_theme` | 窗口深色主题 |
| `browser_path` | 自动探测失败时的浏览器路径 |
| `proxy` | 请求使用的代理 |

模式和优先级在设置标签页里改更方便，其余的手工改文件。改动文件后需重启才生效。

### Telegram

1. 在 [@BotFather](https://t.me/BotFather) 创建机器人并取得令牌。
2. 随便给自己的机器人发一句话，让它看到你的 `chat_id`。
3. 在 `settings.json` 中：

```json
"telegram": {
    "enabled": true,
    "bot_token": "在此填入令牌",
    "chat_ids": [你的CHAT_ID],
    "allow_control": true,
    "notify_critical": true,
    "notify_rewards": true,
    "notify_routine": false,
    "report_every_hours": 6
}
```

4. 验证：`main.py --test-telegram`

`chat_ids` 是白名单。来自其他地方的消息一律忽略，因此陌生人即便找到这个机器人，也无法
控制挖矿程序。

命令：`/status`、`/inventory`、`/campaigns`、`/pause`、`/resume`、
`/switch <频道>`、`/priority add|remove <游戏>`、`/reload`、`/hide`、`/show`、`/reboot`、
`/menu`、`/help`。除了带参数的那两个，其余都有按钮。

## 状态存放在哪里

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        Twitch 令牌
cookies.jar      cookie
settings.json    配置
log.txt          日志（配合 --log）
lock.file        防止同时运行两份
browser_profile  用于登录的浏览器配置文件
```

状态目录按用户存放，而不是放在程序旁边——否则每复制一份都得重新登录。若要反过来
（U 盘、别人的电脑），在 `.exe` 旁放一个空的 `portable.txt`：状态就会存在那里。

## 构建

```bash
env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

三个容易踩的坑：

- 构建前**先停掉正在运行的 `.exe`**，否则会报 `PermissionError`。
- **不要中断构建。** 被打断的 PyInstaller 会留下一个残缺的 `.exe`，运行时死于
  `DLL load failed while importing _tkinter`。看着像代码缺陷，其实不是。
- 没必要就**不要加 `--clean`**——更慢，且没有好处。

## 检查

```bash
main.py --dump-inventory     从真实 Twitch 拉取全部活动
main.py --test-telegram      机器人
tests\bot_check.py           机器人测试（不联网）
tests\live_check.py          内核对真实 Twitch
```

## 结构是怎么搭的

```
core/protocol   关于 Twitch 私有 API 的事实——不是我们的决定
core/config     路径、间隔、上限
core/toolbox    独立的小工具
core/api        网络、重试、韧性
core/identity   令牌与请求头
core/model      活动与掉落
core/channels   频道与观看时长的投递
core/pubsub     订阅
core/miner      只有决策逻辑
auth/           登录：device flow 与通过 CDP 控制浏览器
gui/            窗口与托盘
notify/         Telegram
```

这样切分是有意为之：`protocol` 描述 Twitch 强加的东西（GraphQL persisted query 的哈希、
`minute-watched` 事件的格式、topic 名称），而 `config` 保存我们自己的决定。把两者混在一起，
就等于分不清哪些是可以改的。

浏览器控制是基于 `aiohttp` 自写的 Chrome DevTools Protocol 客户端。刻意不用 Playwright 和
Selenium：两者都会拖来各自的运行时，而本项目的要求是单个自足的 `.exe`。

## 局限

- 仅限 Windows。架构本身不妨碍移植，但浏览器路径、托盘和开机自启都是按 Windows 写的。
- Twitch 并不承诺其私有 API 一成不变。若 persisted query 的哈希变了，要修的是
  `core/protocol.py`。
- 每个进程一个账号。

## 免责声明

本程序所做的，与浏览器里开着一个直播没有区别——只是屏幕前没有人。将观看自动化可能与
Twitch 的服务条款相冲突。风险由使用者自负；作者不对你账号可能承受的后果负责。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
