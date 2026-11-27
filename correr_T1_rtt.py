#!/usr/bin/env python3
"""
T1 - Calibracion de la emulacion: RTT y jitter (srv -> cli).

Diseno:
  * RTT base: ping srv -> cli SIN netem (3 repeticiones), se guarda en base.json.
  * Serie RTT   : RTT_cfg in {0, 10, 50, 100, 200} ms con jitter = 0.
  * Serie jitter: RTT_cfg = 50 ms con jitter in {0, 2, 10} ms.
  * netem en las dos interfaces de r_wan: delay = RTT/2 por sentido y,
    si hay jitter, normal con sigma = J por sentido (RTT = suma de dos
    sentidos independientes -> sigma_RTT = J*sqrt(2)).
  * 3 repeticiones por punto, orden aleatorio, semilla = crc32(nombre).
  * El runner NO analiza: solo guarda ping.txt + meta.json por corrida.

Uso:
  python3 correr_T1_rtt.py             # retoma lo que falte
  python3 correr_T1_rtt.py --rebase    # vuelve a medir el RTT base
  python3 correr_T1_rtt.py --forzar    # repite todas las corridas
"""
import argparse
import json
import re
import subprocess
import time
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np

EXPERIMENTO = "T1-RTT"
DIR_BASE = Path("results") / EXPERIMENTO
VERSION = 2

IP_CLI = "198.18.1.10"
IP_RWAN_ACC = "198.18.1.254"
IP_RWAN_CORE = "198.18.2.253"
IP_RBN_CORE = "198.18.2.254"

N_PINGS = 500          # pings por corrida
INTERVALO_S = 0.05     # intervalo entre pings (>= 0.002 requiere root)
N_BASE_REPS = 3        # repeticiones para medir el RTT base

RTTS = [0, 10, 50, 100, 200]
JITTERS = [0, 2, 10]
RTT_JITTER = 50        # el jitter se prueba solo con RTT = 50 ms
REPS = [1, 2, 3]


# ----------------------------------------------------------------------
# Utilidades de sistema
# ----------------------------------------------------------------------
def ejecutar(cmd, ignorar_error=False):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not ignorar_error:
        raise RuntimeError(f"Fallo: {cmd}\n{res.stderr.strip()}")
    return res.stdout


def obtener_if(cont, ip_obj):
    out = ejecutar(f"docker exec {cont} ip -o -4 addr show")
    for linea in out.splitlines():
        if f"{ip_obj}/" in linea:
            return linea.split()[1]
    raise RuntimeError(f"Sin interfaz {ip_obj} en {cont}")


def ruta_cgroup(cont):
    fmt = "'{{.Id}}'"
    cid = ejecutar(f"docker inspect --format {fmt} {cont}").strip()
    rutas = [
        Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat"),
        Path(f"/sys/fs/cgroup/docker/{cid}/cpu.stat"),
    ]
    for r in rutas:
        if r.exists():
            return r
    return None


def leer_cpu(ruta):
    if ruta is None or not ruta.exists():
        return {}
    datos = {}
    for linea in ruta.read_text().splitlines():
        p = linea.split()
        if len(p) == 2:
            datos[p[0]] = int(p[1])
    return datos


def limpiar_tc(if_bn, if_acc, if_core):
    ejecutar(f"docker exec r_bn tc qdisc del dev {if_bn} root", True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_acc} root", True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_core} root", True)


def configurar_tc(if_bn, if_acc, if_core, rtt_ms, jit_ms):
    """RTT/2 en cada sentido; jitter normal con sigma = J por sentido."""
    limpiar_tc(if_bn, if_acc, if_core)
    if rtt_ms <= 0:
        return
    m = f"{rtt_ms / 2.0:g}"
    if jit_ms > 0:
        opts = f"delay {m}ms {jit_ms:g}ms distribution normal limit 100000"
    else:
        opts = f"delay {m}ms limit 100000"
    ejecutar(f"docker exec r_wan tc qdisc add dev {if_acc} root netem {opts}")
    ejecutar(f"docker exec r_wan tc qdisc add dev {if_core} root netem {opts}")


def parsear_ping(salida):
    res = {}
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received",
                  salida)
    if m:
        res["tx"] = int(m.group(1))
        res["rx"] = int(m.group(2))
    pat = r"rtt min/avg/max/mdev = ([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)"
    m = re.search(pat, salida)
    if m:
        res.update({
            "min_ms": float(m.group(1)),
            "avg_ms": float(m.group(2)),
            "max_ms": float(m.group(3)),
            "mdev_ms": float(m.group(4)),
        })
    else:
        t = [float(x) for x in re.findall(r"time=([\d\.]+) ms", salida)]
        if t:
            res.update({
                "min_ms": float(np.min(t)),
                "avg_ms": float(np.mean(t)),
                "max_ms": float(np.max(t)),
                "mdev_ms": float(np.std(t)),
            })
    return res


def cmd_ping(n):
    return f"docker exec srv ping -n -c {n} -i {INTERVALO_S} {IP_CLI}"


# ----------------------------------------------------------------------
# RTT base (sin netem)
# ----------------------------------------------------------------------
def medir_base(ifs, rebase):
    f_base = DIR_BASE / "base.json"
    if f_base.exists() and not rebase:
        base = json.loads(f_base.read_text())
        print(f"RTT base ya medido: {base['rtt_base_ms']:.4f} ms "
              f"(usar --rebase para repetir)\n")
        return base

    limpiar_tc(*ifs)
    (DIR_BASE / "base").mkdir(parents=True, exist_ok=True)
    reps = []
    for k in range(1, N_BASE_REPS + 1):
        print(f"Midiendo RTT base {k}/{N_BASE_REPS} "
              f"(srv -> cli, {N_PINGS} pings, sin netem)...")
        out = ejecutar(cmd_ping(N_PINGS))
        (DIR_BASE / "base" / f"ping_base_rep{k}.txt").write_text(out)
        reps.append(parsear_ping(out))

    avgs = [r["avg_ms"] for r in reps]
    base = {
        "rtt_base_ms": float(np.mean(avgs)),
        "avg_por_rep_ms": avgs,
        "std_entre_reps_ms": float(np.std(avgs, ddof=1)),
        "n_pings": N_PINGS,
        "reps": reps,
        "hora": datetime.now().isoformat(),
    }
    f_base.write_text(json.dumps(base, indent=4))
    print(f"RTT base = {base['rtt_base_ms']:.4f} ms\n")
    return base


# ----------------------------------------------------------------------
# Validez / reanudacion
# ----------------------------------------------------------------------
def es_valida(d_cor, rtt, jit):
    f_meta = d_cor / "meta.json"
    if not f_meta.exists() or not (d_cor / "ping.txt").exists():
        return False
    try:
        meta = json.loads(f_meta.read_text())
        par = meta.get("parametros", {})
        return (
            meta.get("version") == VERSION
            and par.get("origen") == "srv"
            and par.get("rtt_configurado_ms") == rtt
            and par.get("jitter_configurado_ms") == jit
            and par.get("n_pings") == N_PINGS
        )
    except Exception:
        return False


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebase", action="store_true",
                    help="volver a medir el RTT base")
    ap.add_argument("--forzar", action="store_true",
                    help="repetir todas las corridas")
    args = ap.parse_args()

    print(f"Iniciando {EXPERIMENTO} (srv -> cli)")
    DIR_BASE.mkdir(parents=True, exist_ok=True)

    if_bn = obtener_if("r_bn", IP_RBN_CORE)
    if_acc = obtener_if("r_wan", IP_RWAN_ACC)
    if_core = obtener_if("r_wan", IP_RWAN_CORE)
    ifs = (if_bn, if_acc, if_core)
    print(f"Interfaces: r_bn={if_bn}, r_wan(acc)={if_acc}, "
          f"r_wan(core)={if_core}\n")

    conts = ["cli", "r_wan", "r_bn", "srv"]
    cgroups = {c: ruta_cgroup(c) for c in conts}

    base = medir_base(ifs, args.rebase)
    rtt_base = base["rtt_base_ms"]

    # Puntos: barrido de RTT con J=0 + barrido de jitter con RTT=50
    puntos = [(r, 0) for r in RTTS]
    puntos += [(RTT_JITTER, j) for j in JITTERS if j > 0]
    corridas = [(r, j, rep) for (r, j) in puntos for rep in REPS]

    rng = np.random.default_rng(zlib.crc32(EXPERIMENTO.encode("utf-8")))
    corridas = [corridas[i] for i in rng.permutation(len(corridas))]

    total = len(corridas)
    for idx, (rtt, jit, rep) in enumerate(corridas, start=1):
        nombre = f"RTT{rtt}_J{jit}_rep{rep}"
        d_cor = DIR_BASE / nombre

        if es_valida(d_cor, rtt, jit) and not args.forzar:
            print(f"[{idx}/{total}] {nombre}: ok, salteando...")
            continue

        sem = zlib.crc32(nombre.encode("utf-8"))
        print(f"[{idx}/{total}] {nombre} (sem={sem}) -> {N_PINGS} pings...")
        d_cor.mkdir(parents=True, exist_ok=True)
        (d_cor / "meta.json").unlink(missing_ok=True)

        try:
            configurar_tc(if_bn, if_acc, if_core, rtt, jit)

            tc_antes = ejecutar(f"docker exec r_wan tc qdisc show dev {if_acc}")
            if rtt > 0 and "netem" not in tc_antes:
                raise RuntimeError("netem no quedo aplicado en r_wan")

            cpu0 = {c: leer_cpu(r) for c, r in cgroups.items()}
            t0 = time.perf_counter()
            out_p = ejecutar(cmd_ping(N_PINGS))
            dur = time.perf_counter() - t0
            cpu1 = {c: leer_cpu(r) for c, r in cgroups.items()}

            (d_cor / "ping.txt").write_text(out_p)
            res_p = parsear_ping(out_p)

            tc_acc = ejecutar(f"docker exec r_wan tc -s qdisc show dev {if_acc}")
            tc_core = ejecutar(
                f"docker exec r_wan tc -s qdisc show dev {if_core}")

            cpu_d = {}
            for c in cgroups:
                if cpu0[c] and cpu1[c]:
                    du = cpu1[c].get("usage_usec", 0) - cpu0[c].get(
                        "usage_usec", 0)
                    dt = cpu1[c].get("nr_throttled", 0) - cpu0[c].get(
                        "nr_throttled", 0)
                    cpu_d[c] = {
                        "usage_usec": du,
                        "nr_throttled": dt,
                        "util_nucleos": du / 1e6 / dur,
                    }

            # Chequeos de cordura
            rx = res_p.get("rx", 0)
            tx = res_p.get("tx", 0)
            chequeos = {
                "tx_ok": tx == N_PINGS,
                "perdida_pct": 100.0 * (tx - rx) / tx if tx else None,
                "sin_perdida": rx == N_PINGS,
                "duracion_s": dur,
            }
            if not chequeos["sin_perdida"]:
                raise RuntimeError(
                    f"Se perdieron pings ({rx}/{tx}); corrida descartada")

            meta = {
                "version": VERSION,
                "experimento": EXPERIMENTO,
                "corrida": nombre,
                "semilla": sem,
                "hora": datetime.now().isoformat(),
                "parametros": {
                    "origen": "srv",
                    "destino": "cli",
                    "rtt_configurado_ms": rtt,
                    "jitter_configurado_ms": jit,
                    "delay_por_sentido_ms": rtt / 2.0,
                    "jitter_por_sentido_ms": jit,
                    "distribucion": "normal" if jit > 0 else "fija",
                    "n_pings": N_PINGS,
                    "intervalo_s": INTERVALO_S,
                    "rtt_base_ms": rtt_base,
                },
                "repeticion": rep,
                "ping_resumen": res_p,
                "chequeos": chequeos,
                "cpu_cgroup_delta": cpu_d,
                "tc_estado": {
                    "r_wan_acc": tc_acc.strip(),
                    "r_wan_core": tc_core.strip(),
                },
            }
            (d_cor / "meta.json").write_text(json.dumps(meta, indent=4))

        except Exception as e:
            print(f"[{idx}/{total}] ERROR en {nombre}: {e}")

    limpiar_tc(*ifs)
    print("Barrido T1 (RTT / jitter) finalizado.")


if __name__ == "__main__":
    main()
