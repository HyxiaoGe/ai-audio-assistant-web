#!/usr/bin/env bash
# 旁路监控:压测期间每 N 秒经 ssh 采靶机体征 + 本机探生产邻居,逐行追加 TSV。
# 只读,不写任何 dev/prod 数据。越阈值时 stderr 红字告警(提示人工熄火)。
# 全部靶机/容器/邻居经环境变量注入,脚本内零硬编码。
set -euo pipefail

# ---- 纯函数(可被 sample.test.sh source 后单测)----

# docker stats MemUsage 形如 "45.6MiB" / "1.2GiB";echo 整数 MB。
mem_mb() {
  awk -v s="$1" 'BEGIN{
    u=s; sub(/^[0-9.]+/,"",u);
    v=s; sub(/[A-Za-z]+$/,"",v);
    f=1;
    if (u=="GiB"||u=="GB") f=1024;
    else if (u=="KiB"||u=="kB"||u=="KB") f=1/1024;
    else f=1;            # MiB/MB
    printf "%d", v*f;
  }'
}

# value > max 返回 0(越线),否则 1。支持小数。
breach() {
  awk -v a="$1" -v b="$2" 'BEGIN{ exit !((a+0) > (b+0)) }'
}

# part/whole 的整数百分比;whole=0 返 0。
pct() {
  awk -v p="$1" -v w="$2" 'BEGIN{ if ((w+0)==0) print 0; else printf "%d", (p/w)*100 }'
}

# ---- 采样(需要注入的 env;不被单测覆盖,靠 dry-run 验证真实解析)----

sample_once() {
  local ts line cpu load apimem apicpu pgact pgmax rcli
  ts="$(date +%H:%M:%S)"

  # 一次 ssh 取机器 + 容器 + PG + Redis,减少往返。远程失败则字段留空。
  # 意图行为:heredoc 不加引号,使本地 env 变量($LOADTEST_API_CONTAINER 等)在本机展开后传给远端 bash;远端变量用 \$ 转义以在远端求值。
  # shellcheck disable=SC2087
  line="$(ssh "$LOADTEST_SSH" bash -s <<REMOTE 2>/dev/null || true
set -uo pipefail
cpu=\$(top -bn1 2>/dev/null | awk -F',' '/%Cpu|Cpu\(s\)/{for(i=1;i<=NF;i++) if(\$i ~ /id/){gsub(/[^0-9.]/,"",\$i); printf "%.0f", 100-\$i}}')
load=\$(awk '{print \$1}' /proc/loadavg)
apimem=\$(docker stats --no-stream --format '{{.MemUsage}}' "$LOADTEST_API_CONTAINER" 2>/dev/null | awk '{print \$1}')
apicpu=\$(docker stats --no-stream --format '{{.CPUPerc}}' "$LOADTEST_API_CONTAINER" 2>/dev/null | tr -d '%')
pgact=\$(docker exec "$LOADTEST_PG_CONTAINER" psql -U admin -d audio_assistant -tA -c "select count(*) from pg_stat_activity where state='active'" 2>/dev/null)
pgmax=\$(docker exec "$LOADTEST_PG_CONTAINER" psql -U admin -d audio_assistant -tA -c "show max_connections" 2>/dev/null)
rcli=\$(docker exec "$LOADTEST_REDIS_CONTAINER" redis-cli INFO clients 2>/dev/null | awk -F: '/connected_clients/{gsub(/\r/,"",\$2); print \$2}')
echo "\$cpu|\$load|\$apimem|\$apicpu|\$pgact|\$pgmax|\$rcli"
REMOTE
)"
  IFS='|' read -r cpu load apimem apicpu pgact pgmax rcli <<<"$line"

  # 生产邻居探针(从本机经其公网 URL,反映真实用户视角)——防线③核心信号。
  local neigh_code neigh_ms neigh_ms_int
  read -r neigh_code neigh_ms < <(curl -o /dev/null -s -w '%{http_code} %{time_total}\n' --max-time 5 "$LOADTEST_NEIGHBOR_URL" || echo "000 99")
  neigh_ms_int="$(awk -v s="$neigh_ms" 'BEGIN{printf "%d", s*1000}')"

  local api_mb pg_pct flags=""
  api_mb="$(mem_mb "${apimem:-0MiB}")"
  pg_pct="$(pct "${pgact:-0}" "${pgmax:-1}")"

  breach "${cpu:-0}" "$CPU_MAX"             && flags+="CPU "
  breach "$api_mb" "$API_MEM_MAX_MB"        && flags+="API_MEM "
  breach "$pg_pct" "$PG_CONN_MAX_PCT"       && flags+="PG_CONN "
  breach "$neigh_ms_int" "$NEIGHBOR_MAX_MS" && flags+="NEIGHBOR! "
  [[ "$neigh_code" != "200" ]]              && flags+="NEIGHBOR_DOWN! "

  printf '%s\t%s\t%s\t%sMB\t%s\t%s/%s(%s%%)\t%s\t%sms(%s)\t%s\n' \
    "$ts" "${cpu:-NA}" "${load:-NA}" "$api_mb" "${apicpu:-NA}" \
    "${pgact:-NA}" "${pgmax:-NA}" "$pg_pct" "${rcli:-NA}" \
    "$neigh_ms_int" "$neigh_code" "${flags:-OK}" | tee -a "$OUT" >&2

  if [[ -n "$flags" ]]; then
    printf '\033[31m[告警 %s] 体征越线: %s — 考虑立即熄火(Ctrl-C k6)\033[0m\n' "$ts" "$flags" >&2
  fi
}

main() {
  : "${LOADTEST_SSH:?需设 LOADTEST_SSH,如 dev(~/.ssh/config 中 Host 别名)}"
  : "${LOADTEST_API_CONTAINER:?需设 LOADTEST_API_CONTAINER(docker ps 落实)}"
  : "${LOADTEST_PG_CONTAINER:?需设 LOADTEST_PG_CONTAINER}"
  : "${LOADTEST_REDIS_CONTAINER:?需设 LOADTEST_REDIS_CONTAINER}"
  : "${LOADTEST_NEIGHBOR_URL:?需设 LOADTEST_NEIGHBOR_URL(同机生产 health)}"

  INTERVAL="${LOADTEST_SAMPLE_INTERVAL:-3}"
  OUT="${LOADTEST_SAMPLE_OUT:-loadtest/reports/sample-$(date +%Y%m%d-%H%M%S).tsv}"
  CPU_MAX="${LOADTEST_CPU_MAX:-85}"
  API_MEM_MAX_MB="${LOADTEST_API_MEM_MAX_MB:-350}"  # 对 384m 上限留余量
  PG_CONN_MAX_PCT="${LOADTEST_PG_CONN_MAX_PCT:-80}"
  NEIGHBOR_MAX_MS="${LOADTEST_NEIGHBOR_MAX_MS:-1000}"

  printf '# 采样开始 %s  阈值 CPU>%s API_MEM>%sMB PG>%s%% 邻居>%sms\n' \
    "$(date)" "$CPU_MAX" "$API_MEM_MAX_MB" "$PG_CONN_MAX_PCT" "$NEIGHBOR_MAX_MS" | tee -a "$OUT" >&2
  printf '# ts\tcpu%%\tload\tapi_mem\tapi_cpu%%\tpg_active/max(%%)\tredis_clients\tneighbor\tflags\n' | tee -a "$OUT" >&2

  while true; do
    sample_once
    sleep "$INTERVAL"
  done
}

# 仅直接执行时运行 main;被 source(单测)时只暴露纯函数。
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
