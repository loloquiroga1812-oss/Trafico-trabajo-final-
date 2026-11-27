#!/usr/bin/env python3
"""Muestreador compacto de sockets TCP (corre en srv). Sirve para T3 con N flujos, donde hay que seguir la
ventana de CADA conexión cada pocos ms y el archivo crudo de `ss` sería enorme.

Cada muestra es una línea JSON:  {"t": <epoch s>, "f": [[puerto_peer, bytes_acked, cwnd, unacked, rtt_ms, retrans], ...]}
Solo guarda conexiones con más de 20 kB reconocidos (descarta la conexión de control de iperf3).
Uso: muestreo_ss.py --intervalo-ms 50 --salida /tmp/sst.jsonl [-- comando alternativo]
Se detiene con pkill (el archivo se vacía a disco en cada muestra).
"""
import argparse
import json
import re
import subprocess
import time

CMD = ["ss", "-tin", "state", "established", "sport", "=", ":5201"]
MIN_BYTES = 20000


def _num(patron, texto, tipo=int):
    m = re.search(patron, texto)
    return tipo(m.group(1)) if m else None


def compactar(texto, min_bytes=MIN_BYTES):
    """Texto de `ss -tin` -> lista de [puerto_peer, bytes_acked, cwnd, unacked, rtt_ms, retrans]."""
    filas = []
    for bloque in re.split(r"(?m)^(?=\S)", texto):
        lineas = bloque.strip("\n").splitlines()
        plano = " ".join(bloque.split())
        if not lineas or "cwnd:" not in plano:
            continue
        peer = lineas[0].split()[-1]
        if ":" not in peer:
            continue
        ba = _num(r"\bbytes_acked:(\d+)", plano)
        if ba is not None and ba < min_bytes:
            continue
        filas.append([int(peer.rsplit(":", 1)[1]), ba, _num(r"\bcwnd:(\d+)", plano),
                      _num(r"\bunacked:(\d+)", plano) or 0, _num(r"\brtt:([\d.]+)/", plano, float),
                      _num(r"\bretrans:\d+/(\d+)", plano) or 0])
    return sorted(filas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervalo-ms", type=float, required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = (a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd) or CMD
    paso = a.intervalo_ms / 1000.0
    sig = time.perf_counter()
    with open(a.salida, "w") as f:
        while True:
            t = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True)
            filas = compactar(r.stdout) if r.returncode == 0 else []
            f.write(json.dumps({"t": t, "f": filas}, separators=(",", ":")) + "\n")
            f.flush()
            sig += paso
            resto = sig - time.perf_counter()
            if resto > 0:
                time.sleep(resto)
            else:
                sig = time.perf_counter()


if __name__ == "__main__":
    main()
