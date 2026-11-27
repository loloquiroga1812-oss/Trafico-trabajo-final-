#!/usr/bin/env bash
set -e

apagar_offloads() {
    local cont="$1"
    local ifaces
    ifaces=$(docker exec "$cont" ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1)
    for iface in $ifaces; do
        if [ "$iface" != "lo" ]; then
            docker exec "$cont" ethtool -K "$iface" gro off gso off tso off 2>/dev/null || true
        fi
    done
}

echo "=== Configurando red Parte 1 (TCP) ==="

# Routers
docker exec r_wan ip route del default 2>/dev/null || true
docker exec r_wan ip route replace 198.18.3.0/24 via 198.18.2.254
apagar_offloads r_wan

docker exec r_bn ip route del default 2>/dev/null || true
docker exec r_bn ip route replace 198.18.1.0/24 via 198.18.2.253
apagar_offloads r_bn

# Cliente (198.18.1.10)
docker exec cli ip route del default 2>/dev/null || true
docker exec cli ip route replace 198.18.0.0/15 via 198.18.1.254
apagar_offloads cli

# Servidor iperf3 (198.18.3.10)
docker exec srv ip route del default 2>/dev/null || true
docker exec srv ip route replace 198.18.0.0/15 via 198.18.3.254
apagar_offloads srv

echo "=== Verificando conectividad Parte 1 ==="
docker exec cli ping -c 2 -W 1 198.18.3.10 >/dev/null
echo "OK: cli <-> srv funcionando"
