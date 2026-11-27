#!/usr/bin/env python3
"""Muestreador genérico (corre dentro de un contenedor).

Ejecuta un comando cada N ms y guarda la salida CRUDA con marca de tiempo, una línea JSON por muestra:
    {"t": <epoch s>, "rc": <código>, "out": "<stdout>"}
Se usa para ss (cwnd, srtt, ventana efectiva) y para tc -s -j (ocupación de la cola del tbf).
Uso: muestreo.py --intervalo-ms 100 --salida /tmp/x.jsonl -- tc -s -j qdisc show dev eth1
Se detiene con pkill (el archivo se vacía a disco en cada muestra).
"""
import argparse
import json
import subprocess
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervalo-ms", type=float, required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    paso = a.intervalo_ms / 1000.0
    sig = time.perf_counter()
    with open(a.salida, "w") as f:
        while True:
            t = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True)
            f.write(json.dumps({"t": t, "rc": r.returncode, "out": r.stdout}) + "\n")
            f.flush()
            sig += paso
            resto = sig - time.perf_counter()
            if resto > 0:
                time.sleep(resto)
            else:
                sig = time.perf_counter()


if __name__ == "__main__":
    main()
