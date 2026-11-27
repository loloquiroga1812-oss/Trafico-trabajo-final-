#!/bin/sh
# RTT distinto por flujo (T5b). Corre en r_wan. No toca tc.sh.
#   tc5.sh rtts RTT1_MS RTT2_MS PUERTO1 PUERTO2 [LIMIT_PKTS]
# En cada interfaz de r_wan: qdisc prio con tres bandas; banda 1 y 2 = netem con el retardo de cada flujo;
# banda 3 = el resto (ARP, etc.). Un filtro u32 por puerto TCP elige la banda:
#   datos (srv -> cli, salen por la interfaz hacia net_acc):   puerto ORIGEN  = puerto del servidor iperf3
#   ACK   (cli -> srv, salen por la interfaz hacia net_core):  puerto DESTINO = puerto del servidor iperf3
# Cada sentido lleva RTT/2, igual que tc.sh. Se usa después de `tc.sh clear`.
set -eu

iface_de() { ip -o -4 addr show | awk -v p="$1" 'index($4,p)==1 {print $2; exit}'; }

rtts() {
  r1=$1; r2=$2; p1=$3; p2=$4; limit=${5:-100000}
  h1=$(awk -v r="$r1" 'BEGIN{printf "%.3f", r/2}')
  h2=$(awk -v r="$r2" 'BEGIN{printf "%.3f", r/2}')
  acc=$(iface_de 198.18.1.); core=$(iface_de 198.18.2.)
  for dev in "$acc" "$core"; do
    tc qdisc replace dev "$dev" root handle 1: prio bands 3 priomap 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2
    tc qdisc add dev "$dev" parent 1:1 handle 11: netem limit "$limit" delay "${h1}ms"
    tc qdisc add dev "$dev" parent 1:2 handle 12: netem limit "$limit" delay "${h2}ms"
  done
  tc filter add dev "$acc" protocol ip parent 1: prio 1 u32 match ip sport "$p1" 0xffff flowid 1:1
  tc filter add dev "$acc" protocol ip parent 1: prio 2 u32 match ip sport "$p2" 0xffff flowid 1:2
  tc filter add dev "$core" protocol ip parent 1: prio 1 u32 match ip dport "$p1" 0xffff flowid 1:1
  tc filter add dev "$core" protocol ip parent 1: prio 2 u32 match ip dport "$p2" 0xffff flowid 1:2
}

cmd=${1:-}; [ $# -gt 0 ] && shift
case "$cmd" in
  rtts) rtts "$@" ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
