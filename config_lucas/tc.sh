#!/bin/sh
# Uso (dentro del contenedor, vía docker exec):
#   tc.sh wan RTT_MS [JITTER_MS] [LOSS_PCT] [LIMIT_PKTS]   -> en r_wan
#   tc.sh bn  RATE_MBIT BUF_BYTES [BURST_BYTES]            -> en r_bn
#   tc.sh clear | show | ifbn      (ifbn: nombre de la interfaz del cuello de botella, en r_bn)
# netem: RTT/2 por sentido; jitter normal por sentido; pérdida solo en sentido de datos
# (srv -> cli, o sea egress de r_wan hacia net_acc). El jitter puede reordenar paquetes.
set -eu

iface_de() { ip -o -4 addr show | awk -v p="$1" 'index($4,p)==1 {print $2; exit}'; }

wan() {
  rtt=$1; jit=${2:-0}; loss=${3:-0}; limit=${4:-100000}
  half=$(awk -v r="$rtt" 'BEGIN{printf "%.3f", r/2}')
  acc=$(iface_de 198.18.1.); core=$(iface_de 198.18.2.)
  dl=""
  if awk -v h="$half" -v j="$jit" 'BEGIN{exit !(h>0 || j>0)}'; then
    dl="delay ${half}ms"
    if awk -v j="$jit" 'BEGIN{exit !(j>0)}'; then dl="$dl ${jit}ms distribution normal"; fi
  fi
  ls=""
  if awk -v l="$loss" 'BEGIN{exit !(l>0)}'; then ls="loss ${loss}%"; fi
  # sentido ACK (cli -> srv): sale por la interfaz hacia net_core, sin pérdida
  tc qdisc replace dev "$core" root netem limit "$limit" $dl
  # sentido datos (srv -> cli): sale por la interfaz hacia net_acc, con pérdida
  tc qdisc replace dev "$acc" root netem limit "$limit" $dl $ls
}

bn() {
  rate=$1; buf=$2; burst=${3:-}
  if [ -z "$burst" ]; then   # 0,5 ms de tráfico a la tasa C, mínimo ~2 tramas
    burst=$(awk -v r="$rate" 'BEGIN{b=r*1e6/8/2000; if (b<3200) b=3200; printf "%d", b}')
  fi
  core=$(iface_de 198.18.2.)
  tc qdisc replace dev "$core" root tbf rate "${rate}mbit" burst "$burst" limit "$buf"
}

clear_all() {
  for n in 198.18.1. 198.18.2. 198.18.3.; do
    i=$(iface_de "$n" || true)
    if [ -n "$i" ]; then
      tc qdisc del dev "$i" root 2>/dev/null || true
    fi
  done
  return 0
}

show() {
  for n in 198.18.1. 198.18.2. 198.18.3.; do
    i=$(iface_de "$n" || true)
    if [ -n "$i" ]; then
      echo "== $i ($n*)"
      tc -s qdisc show dev "$i"
    fi
  done
  return 0
}

cmd=${1:-}; [ $# -gt 0 ] && shift
case "$cmd" in
  wan) wan "$@" ;;
  bn) bn "$@" ;;
  clear) clear_all ;;
  show) show ;;
  ifbn) iface_de 198.18.2. ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
