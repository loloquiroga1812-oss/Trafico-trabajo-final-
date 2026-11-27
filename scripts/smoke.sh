#!/bin/sh
# Prueba de humo de la infraestructura (desde la raíz del repo).
set -eu
dc="docker compose"
$dc up -d --build --wait

echo "--- rutas (no debe haber default) ---"
for n in cli r_wan r_bn srv; do echo "[$n]"; $dc exec -T $n ip route; done

echo "--- tc: RTT 40 ms, C=100 Mbit, buffer 250 kB ---"
$dc exec -T r_wan /config/tc.sh wan 40 0 0
$dc exec -T r_bn /config/tc.sh bn 100 250000

echo "--- ping (base + 40 ms esperado) ---"
$dc exec -T cli ping -c 10 -i 0.2 198.18.3.10 | tail -3

echo "--- iperf3 -R cubic, 10 s ---"
$dc exec -T -d srv iperf3 -s
sleep 1
$dc exec -T cli iperf3 -c 198.18.3.10 -R -t 10 -O 3 -C cubic | tail -6

echo "--- tc en r_bn ---"
$dc exec -T r_bn /config/tc.sh show
$dc exec -T srv pkill -x iperf3 || true
