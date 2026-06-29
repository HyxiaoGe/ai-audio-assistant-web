#!/usr/bin/env bash
# source sample.sh 取纯函数(main 受 BASH_SOURCE 守卫,不会运行)。
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 文件在运行时由 SCRIPT_DIR 正确定位;shellcheck 从调用目录解析 ./sample.sh 失败是路径误报。
# shellcheck disable=SC1091
# shellcheck source=./sample.sh
source "$SCRIPT_DIR/sample.sh"
set +e  # source 会带入 set -e;关掉以便用退出码做断言

fail=0
check() { if [[ "$2" == "$3" ]]; then echo "ok: $1"; else echo "FAIL: $1 期望[$3] 实得[$2]"; fail=1; fi; }

check "mem_mb MiB"   "$(mem_mb 45.6MiB)" "45"
check "mem_mb GiB"   "$(mem_mb 1.2GiB)"  "1228"
check "mem_mb KiB"   "$(mem_mb 2048KiB)" "2"
breach 90 85; check "breach 越线返0" "$?" "0"
breach 10 85; check "breach 未越返1" "$?" "1"
breach 85.4 85; check "breach 小数越线" "$?" "0"
check "pct 40/50"    "$(pct 40 50)" "80"
check "pct 除0保护"  "$(pct 5 0)"   "0"

if [[ "$fail" == "0" ]]; then echo "sample.test.sh 全部通过"; fi
exit "$fail"
