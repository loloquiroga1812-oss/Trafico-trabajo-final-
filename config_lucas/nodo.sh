#!/bin/sh
# Arranque común de todos los nodos: sin ruta por defecto, rutas estáticas, sin offloads.
set -eu

ip route del default 2>/dev/null || true

if [ -n "${ROUTES:-}" ]; then
  echo "$ROUTES" | tr ';' '\n' | while read -r dst _via gw; do
    [ -n "$dst" ] && ip route replace "$dst" via "$gw"
  done
fi

# Cada paquete debe ser una trama real de 1514 B (los contadores de tc cuentan tramas)
for d in $(ls /sys/class/net | grep -v '^lo$'); do
  ethtool -K "$d" gro off gso off tso off >/dev/null 2>&1 || echo "AVISO: no pude apagar offloads en $d"
done

touch /tmp/listo
exec sleep infinity
