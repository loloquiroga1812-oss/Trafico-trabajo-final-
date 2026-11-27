#!/bin/sh
# Preparación del host (correr con sudo). Cambios no persistentes (se pierden al reiniciar).
set -u
modprobe tcp_bbr && echo "tcp_bbr OK" || echo "AVISO: tcp_bbr no disponible"
modprobe br_netfilter 2>/dev/null || true
for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > "$g"; done
echo "governor: $(sort -u /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | tr '\n' ' ')"
if [ -w /sys/devices/system/cpu/intel_pstate/no_turbo ]; then
  echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo
  echo "no_turbo=$(cat /sys/devices/system/cpu/intel_pstate/no_turbo)"
fi
# iperf3 -w usa SO_SNDBUF/SO_RCVBUF, que Linux recorta a net.core.{w,r}mem_max (212992 por defecto).
# Sin esto, las ventanas de T2 mayores a ~200 kB quedarían recortadas en silencio.
sysctl -w net.core.rmem_max=16777216 net.core.wmem_max=16777216
echo "--- CPUs y hermanos SMT ---"
lscpu -e=CPU,CORE,ONLINE,MAXMHZ
cat /sys/fs/cgroup/cgroup.controllers
