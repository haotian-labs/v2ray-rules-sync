#!/usr/bin/env bash

# sync-rules.sh
# 定时从 Loyalsoldier/v2ray-rules-dat 拉取 geoip.dat / geosite.dat，
# 用 v2dat 解包为 mosdns domain_set / ip_set 使用的三个 txt。
#
# 依赖: Bash、curl 或 wget、标准 Unix 工具（mktemp / cmp / chmod / mv 等）、v2dat（https://github.com/urlesistiana/v2dat）
# 安装 v2dat: go install github.com/urlesistiana/v2dat@latest
#
# 下载与解包在 $RULES_DIR 下的随机临时目录进行，成功后逐文件原子替换。
#
# crontab 示例（每天 4:15）:
#   15 4 * * * /opt/mosdns/sync-rules.sh >> /opt/mosdns/logs/sync-rules.log 2>&1

set -euo pipefail

GEOSITE_URL='https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat'
GEOIP_URL='https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat'
# 可通过环境变量覆盖（例如 CI 中设为仓库内目录）
RULES_DIR="${RULES_DIR:-/opt/mosdns/rules}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

need_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "未找到命令: $1"
}

for cmd in mktemp chmod mv cmp v2dat; do
	need_cmd "$cmd"
done
if command -v curl >/dev/null 2>&1; then
	DOWNLOADER=curl
elif command -v wget >/dev/null 2>&1; then
	DOWNLOADER=wget
else
	die "需要 curl 或 wget"
fi

FILES=(geosite_cn.txt 'geosite_geolocation-!cn.txt' geoip_cn.txt)
WORK=''
LOCK_HELD=0
mkdir -p -- "$RULES_DIR"
# 转为绝对路径，确保相对目录也能安全用于临时文件与清理。
RULES_DIR="$(cd -- "$RULES_DIR" && pwd -P)"
LOCK_DIR="$RULES_DIR/.rules-update.lock"

cleanup() {
	local status=$?
	trap - EXIT
	if [[ -n "$WORK" ]]; then
		rm -rf -- "$WORK" || log "WARN: 无法清理临时目录: $WORK" >&2
	fi
	if [[ "$LOCK_HELD" == 1 ]]; then
		rmdir -- "$LOCK_DIR" || log "WARN: 无法清理更新锁: $LOCK_DIR" >&2
	fi
	exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# 两个脚本共用同一把锁，避免同时向同一规则目录发布。
mkdir -- "$LOCK_DIR" 2>/dev/null || die "无法获取更新锁: ${LOCK_DIR}（已有任务运行或目录不可写）"
LOCK_HELD=1
WORK="$(mktemp -d "$RULES_DIR/.rules-work.XXXXXX")"

download() {
	local url="$1" out="$2"
	if [[ "$DOWNLOADER" == curl ]]; then
		curl -fsSL --connect-timeout 30 --max-time 600 \
			--retry 3 --retry-delay 2 --retry-connrefused \
			-o "$out" "$url" || die "下载失败: ${out##*/}"
	else
		wget --no-verbose --timeout=30 --tries=3 -O "$out" "$url" \
			|| die "下载失败: ${out##*/}"
	fi
	[[ -s "$out" ]] || die "下载结果为空: ${out##*/}"
}

publish_rules() {
	local file updated=0
	# 全部校验并设置权限后才开始替换，避免下载或解包失败影响现有规则。
	for file in "${FILES[@]}"; do
		[[ -s "$WORK/$file" ]] || die "规则缺少或为空: $file"
		[[ ! -d "$RULES_DIR/$file" ]] || die "目标路径是目录: $RULES_DIR/$file"
		chmod 0644 "$WORK/$file"
	done
	for file in "${FILES[@]}"; do
		if [[ -f "$RULES_DIR/$file" ]] && cmp -s "$WORK/$file" "$RULES_DIR/$file"; then
			continue
		fi
		# 临时目录与目标在同一文件系统；每个文件通过 rename 原子替换。
		mv -f -- "$WORK/$file" "$RULES_DIR/$file"
		updated=$((updated + 1))
	done
	log "规则更新完成: ${RULES_DIR}（更新 $updated 个文件，未变化 $(( ${#FILES[@]} - updated )) 个）"
}

log "下载 geosite.dat"
download "$GEOSITE_URL" "$WORK/geosite.dat"
log "下载 geoip.dat"
download "$GEOIP_URL" "$WORK/geoip.dat"

log "解包 geosite (cn)"
v2dat unpack geosite -o "$WORK" -f cn "$WORK/geosite.dat"
log "解包 geosite (geolocation-!cn)"
v2dat unpack geosite -o "$WORK" -f 'geolocation-!cn' "$WORK/geosite.dat"
log "解包 geoip (cn)"
v2dat unpack geoip -o "$WORK" -f cn "$WORK/geoip.dat"

publish_rules
