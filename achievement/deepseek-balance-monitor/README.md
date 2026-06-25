# DeepSeek 余额监控

Windows 系统托盘应用，实时监控 DeepSeek API 账户余额。

## 功能

- 🟢 系统托盘常驻，图标颜色反映余额状态（绿/黄/红）
- 🔄 定时自动查询余额（默认每 5 分钟）
- 📊 本地 SQLite 存储余额历史
- ⚠️ 余额低于阈值时弹 Windows 原生通知告警
- 📝 右键菜单查看历史记录、修改设置

## 配置

编辑 `config.json`：

```json
{
    "api_key": "sk-xxxxxxxxxxxxxxxx",   // DeepSeek API Key
    "check_interval_minutes": 5,        // 检查间隔（分钟）
    "alert_threshold": 10.0,            // 余额告警阈值（元）
    "currency": "CNY"                   // 货币单位
}
```

或者设置环境变量 `DEEPSEEK_API_KEY` 来提供 API Key（优先级高于配置文件）。

## 使用

双击 `run.bat` 启动（无命令行窗口），或运行 `run_debug.bat` 查看调试输出。

托盘图标右键菜单：
- **余额显示** — 当前余额（不可点击）
- **立即刷新** — 手动查询一次
- **查看历史** — 打开最近 30 条记录
- **打开设置** — 用记事本打开 config.json
- **关于** — 显示应用信息
- **退出** — 关闭应用

### 开机自启

将 `run.bat` 的快捷方式放到启动文件夹：
1. `Win + R`，输入 `shell:startup`
2. 把 `run.bat` 创建快捷方式拖进去

## 图标颜色说明

| 颜色 | 含义 |
|------|------|
| 🟢 绿色 | 余额充足（> 2 倍阈值） |
| 🟡 黄色 | 余额偏低（1~2 倍阈值之间） |
| 🔴 红色 | 余额不足（≤ 阈值） |
| ⚪ 灰色 | 尚未获取数据 / 查询出错 |

## 依赖

- Python 3.10+
- pystray (系统托盘)
- Pillow (图标生成)
- requests (API 调用)
- winotify (Windows 通知)

安装：`pip install -r requirements.txt`
