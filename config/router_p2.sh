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

echo "=== Configurando red Parte 2 (Colas HTTP) ==="

# Routers
docker exec r_wan ip route del default 2>/dev/null || true
docker exec r_wan ip route replace 198.18.3.0/24 via 198.18.2.254
apagar_offloads r_wan

docker exec r_bn ip route del default 2>/dev/null || true
docker exec r_bn ip route replace 198.18.1.0/24 via 198.18.2.253
apagar_offloads r_bn

# Generador de carga (198.18.1.20)
docker exec gen ip route del default 2>/dev/null || true
docker exec gen ip route replace 198.18.0.0/15 via 198.18.1.254
apagar_offloads gen

# Balanceador HAProxy (198.18.3.20)
docker exec lb ip route del default 2>/dev/null || true
docker exec lb ip route replace 198.18.0.0/15 via 198.18.3.254
apagar_offloads lb

# Servidores FastAPI (198.18.3.11 .. 198.18.3.14)
for api in api1 api2 api3 api4; do
    docker exec "$api" ip route del default 2>/dev/null || true
    docker exec "$api" ip route replace 198.18.0.0/15 via 198.18.3.254
    apagar_offloads "$api"
done

echo "=== Verificando conectividad Parte 2 ==="
docker exec gen ping -c 2 -W 1 198.18.3.20 >/dev/null
docker exec gen ping -c 2 -W 1 198.18.3.11 >/dev/null
docker exec gen ping -c 2 -W 1 198.18.3.14 >/dev/null
echo "OK: gen <-> lb y api1..api4 funcionando"
