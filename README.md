# v2ray-rules-sync

从 [Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat) 拉取 `geoip.dat` / `geosite.dat`，用 [v2dat](https://github.com/urlesistiana/v2dat) 解包为 mosdns 等工具可用的域名 / IP 列表文本。

## 生成文件

规则文件在分支 **`release`** 根目录；以下为 GitHub Raw 直链（将 `release` 换为标签名如 `sync-2026-04-17` 可固定到某次同步）。

| 文件 | 说明 | 下载 |
|------|------|------|
| `geosite_cn.txt` | geosite 列表 `cn` | [Raw](https://raw.githubusercontent.com/jianghaotian/v2ray-rules-sync/release/geosite_cn.txt) |
| `geosite_geolocation-!cn.txt` | geosite 列表 `geolocation-!cn` | [Raw](https://raw.githubusercontent.com/jianghaotian/v2ray-rules-sync/release/geosite_geolocation-%21cn.txt) |
| `geoip_cn.txt` | geoip 列表 `cn` | [Raw](https://raw.githubusercontent.com/jianghaotian/v2ray-rules-sync/release/geoip_cn.txt) |

## GitHub Actions

工作流：[`.github/workflows/sync-rules.yml`](.github/workflows/sync-rules.yml)

- **定时**：每天 **UTC 14:00**（**北京时间 22:00**）
- **手动**：在仓库 **Actions** 中选择 **Sync v2ray rules** → **Run workflow**
- **分支**：**`release`** 上**仅包含上述三个 txt**（根目录）。**远端尚无 `release` 时**用 orphan 创建（与 `main` 无共同祖先）；**已存在 `release` 时**在现有历史上**追加 commit**，形成按同步时间增长的线性历史。推送为普通 `git push`（快进）。
- **标签**：每次成功同步会打 **`sync-YYYY-MM-DD`**（日期为**北京时间**）；同一天多次同步会移动该标签指向最新提交

**`main`** 保留脚本与文档；**订阅规则请只用 `release` 分支或对应 tag**（例如 Raw、Release 下载等）。

## 本地执行

提供两种方式，按需选择一种即可：

| 脚本 | 用途 | 依赖 |
|------|------|------|
| [`download-rules.sh`](download-rules.sh) | 直接下载本仓库已生成的三个 txt，适合服务器定时更新 | Bash、`curl` 或 `wget`、标准 Unix 工具（`mktemp`、`cmp`、`chmod`、`mv` 等） |
| [`sync-rules.sh`](sync-rules.sh) | 从上游下载 dat 并在本地解包 | 同上，另需 `v2dat` |

两个脚本均会在临时下载错误时重试。下载、解包和非空校验全部完成后，才开始更新规则；内容未变化的文件会跳过，保留原文件的时间戳和权限。更新的文件以 `0644` 权限逐个原子替换，避免读取到写入中的文件；三个文件整体并非原子更新，读取方若要求同一版本，应在脚本成功退出后再统一加载。

两个脚本共用输出目录下的 `.rules-update.lock` 目录锁，同一目录已有更新任务时，后启动的任务会报错退出。临时目录和锁在正常退出及收到 `HUP`、`INT`、`TERM` 信号时自动清理；若进程被强制终止（如 `kill -9`）或系统断电，确认没有更新任务运行后，可手动删除遗留的 `.rules-update.lock` 和 `.rules-work.*` 目录再重试。

### 直接下载已生成的规则

在 `main` 分支获取脚本后执行，无需安装 `v2dat`：

```bash
chmod +x download-rules.sh
# 默认从本仓库 release 分支下载，写入 /opt/mosdns/rules/
sudo ./download-rules.sh
```

可通过环境变量指定输出目录和下载来源：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `RULES_DIR` | `/opt/mosdns/rules` | 规则输出目录，自动创建；执行用户需有写入权限 |
| `BASE_URL` | `https://raw.githubusercontent.com/jianghaotian/v2ray-rules-sync/release` | 三个 txt 所在的根 URL，允许末尾带 `/`；可替换为标签路径或镜像地址 |

```bash
# 下载到当前用户目录
RULES_DIR="$HOME/mosdns/rules" ./download-rules.sh

# 使用指定同步标签下的规则（将标签替换为实际存在的标签）
RULES_DIR="$HOME/mosdns/rules" \
BASE_URL="https://raw.githubusercontent.com/jianghaotian/v2ray-rules-sync/sync-2026-04-17" \
./download-rules.sh
```

脚本先将三个文件下载到输出目录内的随机临时目录，下载失败或结果为空时退出，不覆盖现有规则。

服务器定时任务示例（每天 4:15，按服务器本地时间执行）。先将脚本放到 `/opt/mosdns/download-rules.sh` 并赋予执行权限，创建日志目录：

```bash
sudo mkdir -p /opt/mosdns/logs
```

在有权写入规则目录及日志目录的用户 crontab 中添加：

```cron
15 4 * * * /opt/mosdns/download-rules.sh >> /opt/mosdns/logs/download-rules.log 2>&1
```

### 从上游下载并解包

依赖：`curl` 或 `wget`、[v2dat](https://github.com/urlesistiana/v2dat)（`go install github.com/urlesistiana/v2dat@latest`）

```bash
chmod +x sync-rules.sh
# 默认写入 /opt/mosdns/rules/
sudo ./sync-rules.sh
```

输出目录可通过环境变量 **`RULES_DIR`** 覆盖，例如：

```bash
RULES_DIR="$HOME/mosdns/rules" ./sync-rules.sh
```

如需定时在本地解包，可使用以下任务（每天 4:15，按服务器本地时间执行；需提前创建日志目录，并确保 cron 的 `PATH` 中包含 `v2dat`）：

```cron
15 4 * * * /path/to/sync-rules.sh >> /path/to/sync-rules.log 2>&1
```

## 验证

使用 Python 3.8+ 运行离线回归测试（模拟 `curl`、`wget` 和 `v2dat`，不访问网络）：

```bash
python3 -m unittest discover -s tests -v
```

覆盖正常更新、内容未变化、下载或解包失败、空文件、单文件原子替换、并发锁及终止信号清理。

## 上游与许可

规则数据版权归上游项目；使用前请遵守 [v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat) 及其数据源的许可说明。
