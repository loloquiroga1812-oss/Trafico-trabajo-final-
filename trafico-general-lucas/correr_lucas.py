#!/usr/bin/env python3
"""Runner de la parte 1 (TCP / red emulada). Corre y guarda datos crudos; NO analiza.

Un directorio por corrida:
  results/<exp>/<serie>/<corrida>/{meta.json, iperf.json | sonda.csv, tc_wan.txt, tc_bn.txt}
Si una corrida ya tiene meta.json con status "ok", se saltea: se puede retomar tras un corte.

Uso:
  ./correr.py t1-capacidad            # también: t1-rtt, t1-perdida, t2-ventana, t2-libre
  ./correr.py t2                      # grupos: t1, t2, t3, t4 (pérdida) y t5 (equidad)
  ./correr.py t1 --listar             # lista las corridas y el tiempo estimado, sin correr nada
  ./correr.py t1-capacidad --rapido   # prueba corta; escribe en results-prueba/ (no contamina results/)
"""
import argparse
import json
import os
import platform
import random
import subprocess
import sys
import threading
import time
import traceback
import zlib
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
IP_SRV = "198.18.3.10"
CONTENEDORES = ["cli", "r_wan", "r_bn", "srv"]
ALGOS = ["reno", "cubic", "bbr"]
PAUSA_ENTRE_CORRIDAS_S = 2


# ----------------------------------------------------------------------------- utilidades
def leer_env():
    d = {}
    for linea in (RAIZ / ".env").read_text().splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def sh(cmd, check=True, timeout=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"falló {' '.join(cmd)}: {(r.stderr or r.stdout).strip()[:500]}")
    return r


def dx(cont, *cmd, check=True, timeout=None, detach=False):
    base = ["docker", "exec"] + (["-d"] if detach else []) + [cont]
    return sh(base + list(cmd), check=check, timeout=timeout)


def semilla(exp, serie, nombre):
    """Semilla reproducible derivada del nombre (no de hash(), que cambia entre ejecuciones)."""
    return zlib.crc32(f"{exp}/{serie}/{nombre}".encode()) & 0xFFFFFFFF


_cid = {}


def cpu_stat(cont):
    """Lee cpu.stat del cgroup v2 del contenedor desde el host. None si no se encuentra."""
    if cont not in _cid:
        _cid[cont] = sh(["docker", "inspect", "-f", "{{.Id}}", cont]).stdout.strip()
    cid = _cid[cont]
    for p in (f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat",
              f"/sys/fs/cgroup/docker/{cid}/cpu.stat"):
        if os.path.exists(p):
            d = {}
            for linea in open(p):
                k, v = linea.split()
                d[k] = int(v)
            return d
    return None


SYSFS_CPU = "/sys/devices/system/cpu"


def parse_cpus(txt):
    """'1' -> [1];  '2,3' -> [2, 3];  '2-4' -> [2, 3, 4]"""
    out = []
    for parte in str(txt).split(","):
        parte = parte.strip()
        if "-" in parte:
            a, b = parte.split("-")
            out += list(range(int(a), int(b) + 1))
        elif parte:
            out.append(int(parte))
    return out


def cpus_medidos():
    """CPUs lógicos donde corren las cosas medidas (según .env)."""
    env = leer_env()
    cpus = []
    for k in ("CPU_HOST", "CPU_CLI", "CPU_SRV", "CPU_ROUTERS"):
        cpus += parse_cpus(env.get(k, ""))
    return sorted(set(cpus))


def leer_sysfs(ruta):
    try:
        return open(ruta).read().strip()
    except OSError:
        return None


def estado_cpu_host():
    """Governor y turbo del host (deben ser 'performance' y no_turbo=1 para medir en serio)."""
    gov = leer_sysfs(f"{SYSFS_CPU}/cpu0/cpufreq/scaling_governor")
    nt = leer_sysfs(f"{SYSFS_CPU}/intel_pstate/no_turbo")
    return {"governor": gov, "no_turbo": int(nt) if nt is not None else None}


class MuestreadorFreq:
    """Lee scaling_cur_freq de los CPUs indicados una vez por segundo mientras dura la medición.
    (Una foto antes de medir no sirve: con el CPU en reposo muestra la frecuencia más baja.)"""

    def __init__(self, cpus, periodo_s=1.0):
        self.cpus, self.periodo, self.datos = cpus, periodo_s, {c: [] for c in cpus}
        self._fin = threading.Event()
        self._th = threading.Thread(target=self._bucle, daemon=True)

    def _leer(self):
        for c in self.cpus:
            v = leer_sysfs(f"{SYSFS_CPU}/cpu{c}/cpufreq/scaling_cur_freq")
            if v is not None:
                self.datos[c].append(int(v))

    def _bucle(self):
        while True:
            self._leer()
            if self._fin.wait(self.periodo):
                return

    def __enter__(self):
        self._th.start()
        return self

    def __exit__(self, *a):
        self._fin.set()
        self._th.join(timeout=3)

    def resumen(self):
        return {str(c): {"min": min(v), "media": round(sum(v) / len(v)), "max": max(v), "n": len(v)}
                for c, v in self.datos.items() if v}


# ----------------------------------------------------------------------------- definición de experimentos
def exp_t1_capacidad(a):
    """C x algoritmo, RTT chico, buffer holgado (4 x BDP). Compara goodput con eta*C."""
    rtt = 10
    dur, omit = (8, 3) if a.rapido else (30, 5)
    algos = [x for x in ALGOS if not (a.sin_bbr and x == "bbr")]
    specs = []
    for algo in algos:
        for C in (10, 25, 50, 100):
            bdp = C * 1e6 / 8 * rtt / 1000
            buf = max(int(4 * bdp), 30000)
            for rep in range(1, a.reps + 1):
                specs.append(dict(serie=algo, nombre=f"C{C}_rep{rep}", tipo="iperf",
                                  params=dict(C=C, rtt=rtt, jitter=0, loss=0, buf=buf,
                                              algo=algo, dur=dur, omit=omit)))
    return specs


def exp_t1_rtt(a):
    """RTT configurado vs medido, jitter y RTT base sin netem. Sonda UDP, un eco cada 20 ms."""
    n = 200 if a.rapido else 1000
    iv = 20
    def sonda(**kw):
        return dict(C=100, buf=1_000_000, loss=0, n=n, intervalo_ms=iv, **kw)
    specs = []
    for rep in range(1, a.reps + 1):
        specs.append(dict(serie="base", nombre=f"base_rep{rep}", tipo="sonda",
                          params=sonda(rtt=0, jitter=0, sin_netem=True)))
        for rtt in (0, 10, 50, 100, 200):
            specs.append(dict(serie="rtt", nombre=f"rtt{rtt}_rep{rep}", tipo="sonda",
                              params=sonda(rtt=rtt, jitter=0)))
        for j in (0, 2, 10):
            specs.append(dict(serie="jitter", nombre=f"j{j}_rep{rep}", tipo="sonda",
                              params=sonda(rtt=50, jitter=j)))
    return specs


def exp_t1_perdida(a):
    """Pérdida configurada vs medida con UDP a tasa fija (1000 paquetes/s), RTT 20 ms."""
    n = 5000 if a.rapido else 60000
    specs = []
    for p in (0, 0.1, 1, 5):
        for rep in range(1, a.reps + 1):
            specs.append(dict(serie="perdida", nombre=f"p{p}_rep{rep}", tipo="sonda",
                              params=dict(C=100, buf=1_000_000, rtt=20, jitter=0, loss=p,
                                          n=n, intervalo_ms=1)))
    return specs


def exp_t2_ventana(a):
    """Ventana fija (iperf3 -w) x RTT, C = 100 Mbit/s, buffer holgado (4 MB: nunca hay pérdida).
    Serie 'cubic' = barrido principal. Series 'reno' y 'bbr' = comparación de algoritmos en
    cuatro puntos donde manda la ventana (también están en el barrido de cubic)."""
    C = 100
    dur, omit = (6, 3) if a.rapido else (15, 5)
    Ws = (64, 256, 1024) if a.rapido else (32, 64, 128, 256, 512, 1024)       # kB (KiB, como iperf3 -w)
    rtts = (20, 100) if a.rapido else (10, 20, 50, 100, 200)
    comp = [(64, 20)] if a.rapido else [(64, 20), (64, 100), (128, 50), (256, 100)]
    otros = [x for x in ("reno", "bbr") if not (a.sin_bbr and x == "bbr")]

    def spec(algo, W, rtt, rep):
        return dict(serie=algo, nombre=f"W{W}k_rtt{rtt}_rep{rep}", tipo="iperf",
                    params=dict(C=C, rtt=rtt, jitter=0, loss=0, buf=4_000_000, algo=algo, dur=dur,
                                omit=omit, window_kb=W, muestreos=dict(ss_ms=200)))
    specs = []
    for rep in range(1, a.reps + 1):
        specs += [spec("cubic", W, rtt, rep) for W in Ws for rtt in rtts]
        specs += [spec(algo, W, rtt, rep) for algo in otros for W, rtt in comp]
    return specs


def exp_t2_libre(a):
    """Ventana libre (sin -w), buffer = BDP, RTT 20 ms: ¿llenan el enlace Reno, Cubic y BBR?
    ¿Cuánta cola forma cada uno? Se muestrea la cola del tbf cada 100 ms y el socket (ss) cada 200 ms."""
    C, rtt = 100, 20
    dur, omit = (10, 3) if a.rapido else (60, 10)
    buf = int(C * 1e6 / 8 * rtt / 1000)
    specs = []
    for algo in [x for x in ALGOS if not (a.sin_bbr and x == "bbr")]:
        for rep in range(1, a.reps + 1):
            specs.append(dict(serie=algo, nombre=f"rtt{rtt}_rep{rep}", tipo="iperf",
                              params=dict(C=C, rtt=rtt, jitter=0, loss=0, buf=buf, algo=algo, dur=dur,
                                          omit=omit, muestreos=dict(ss_ms=200, cola_ms=100))))
    return specs


def exp_t3_uno(a):
    """Un flujo, RTT 20 ms, C = 100 Mbit/s, buffer b = B/BDP x algoritmo. 60 s por corrida.
    Se muestrea la cola del tbf cada 100 ms y el socket (ss) cada 200 ms."""
    C, rtt = 100, 20
    dur, omit = (15, 3) if a.rapido else (60, 5)
    bs = (0.1, 1) if a.rapido else (0.05, 0.1, 0.25, 0.5, 1, 2)
    bdp = C * 1e6 / 8 * rtt / 1000
    specs = []
    for algo in [x for x in ALGOS if not (a.sin_bbr and x == "bbr")]:
        for b in bs:
            for rep in range(1, a.reps + 1):
                specs.append(dict(serie=algo, nombre=f"b{b}_rep{rep}", tipo="iperf",
                                  params=dict(C=C, rtt=rtt, jitter=0, loss=0, buf=int(round(b * bdp)), b=b,
                                              algo=algo, dur=dur, omit=omit, flows=1,
                                              muestreos=dict(ss_ms=200, cola_ms=100))))
    return specs


def exp_t3_n(a):
    """N flujos Reno (iperf3 -P N), RTT 20 ms, C = 100 Mbit/s, buffer b = B/BDP. 60 s por corrida.
    Se muestrea la ventana de CADA flujo cada 50 ms (para medir sincronización) y la cola cada 100 ms."""
    C, rtt = 100, 20
    dur, omit = (15, 3) if a.rapido else (60, 5)
    Ns = (4, 16)
    bs = (0.25,) if a.rapido else (0.1, 0.25, 0.5, 1)
    bdp = C * 1e6 / 8 * rtt / 1000
    specs = []
    for N in Ns:
        for b in bs:
            for rep in range(1, a.reps + 1):
                specs.append(dict(serie=f"N{N}", nombre=f"b{b}_rep{rep}", tipo="iperf",
                                  params=dict(C=C, rtt=rtt, jitter=0, loss=0, buf=int(round(b * bdp)), b=b,
                                              algo="reno", dur=dur, omit=omit, flows=N,
                                              muestreos=dict(sst_ms=50, cola_ms=100))))
    return specs


def exp_t4_perdida(a):
    """Pérdida aleatoria (netem, solo en el sentido de los datos) x RTT x algoritmo, buffer = BDP.
    Se muestrea el socket (ss) y se guardan los contadores TCP del kernel antes y después (timeouts, etc.).
    Las corridas a RTT 100 ms duran el doble: a p chico el ciclo de Reno/Cubic dura varios segundos."""
    C = 100
    ps = (0.1, 1) if a.rapido else (0.01, 0.1, 0.5, 1, 2, 5)
    specs = []
    for algo in [x for x in ALGOS if not (a.sin_bbr and x == "bbr")]:
        for rtt in (20, 100):
            dur, omit = (10, 3) if a.rapido else ((30, 5) if rtt == 20 else (60, 5))
            bdp = C * 1e6 / 8 * rtt / 1000
            for p in ps:
                for rep in range(1, a.reps + 1):
                    specs.append(dict(serie=algo, nombre=f"rtt{rtt}_p{p}_rep{rep}", tipo="iperf",
                                      params=dict(C=C, rtt=rtt, jitter=0, loss=p, buf=int(bdp), algo=algo,
                                                  dur=dur, omit=omit, flows=1, muestreos=dict(ss_ms=200),
                                                  netstat=True)))
    return specs


def exp_t5_n(a):
    """(a) N flujos del mismo algoritmo, mismo RTT (20 ms), B = BDP. Índice de Jain con el goodput de cada flujo."""
    C, rtt = 100, 20
    dur, omit = (15, 3) if a.rapido else (60, 10)
    Ns = (4,) if a.rapido else (2, 4, 8)
    bdp = C * 1e6 / 8 * rtt / 1000
    specs = []
    for algo in [x for x in ALGOS if not (a.sin_bbr and x == "bbr")]:
        for N in Ns:
            for rep in range(1, a.reps + 1):
                specs.append(dict(serie=algo, nombre=f"N{N}_rep{rep}", tipo="iperf",
                                  params=dict(C=C, rtt=rtt, jitter=0, loss=0, buf=int(bdp), algo=algo, dur=dur,
                                              omit=omit, flows=N, muestreos=dict(cola_ms=100))))
    return specs


def exp_t5_rtt(a):
    """(b) Dos flujos del mismo algoritmo, uno con RTT 20 ms y el otro con RTT2 (RTT2 = 20 es el CONTROL: debe
    repartir parejo). Dos clientes iperf3 en puertos distintos; tc5.sh da a cada puerto su RTT. En las
    repeticiones pares se intercambian los puertos para no confundir el efecto del RTT con el del orden."""
    C, rtt1 = 100, 20
    dur, omit = (15, 3) if a.rapido else (60, 10)
    rtt2s = (20, 80) if a.rapido else (20, 40, 80, 160)
    bdp = C * 1e6 / 8 * rtt1 / 1000
    specs = []
    for algo in [x for x in ALGOS if not (a.sin_bbr and x == "bbr")]:
        for r2 in rtt2s:
            for rep in range(1, a.reps + 1):
                rtts = [rtt1, r2] if rep % 2 == 1 else [r2, rtt1]
                specs.append(dict(serie=algo, nombre=f"rtt2_{r2}_rep{rep}", tipo="iperf2",
                                  params=dict(C=C, rtt=rtt1, jitter=0, loss=0, buf=int(bdp), algos=[algo, algo],
                                              rtts=rtts, puertos=[5201, 5202], dur=dur, omit=omit,
                                              muestreos=dict(ss_ms=200, ss_puertos=[5201, 5202], cola_ms=100))))
    return specs


def exp_t5_algos(a):
    """(c) Dos algoritmos distintos compiten (mismo RTT, 20 ms) con buffer b = B/BDP. En las repeticiones pares se
    intercambian los puertos."""
    C, rtt = 100, 20
    dur, omit = (15, 3) if a.rapido else (60, 10)
    bs = (1,) if a.rapido else (0.25, 1, 4)
    pares = [("cubic", "reno"), ("bbr", "cubic"), ("bbr", "reno")]
    if a.rapido:
        pares = [("cubic", "reno"), ("bbr", "reno")]
    if a.sin_bbr:
        pares = [x for x in pares if "bbr" not in x]
    bdp = C * 1e6 / 8 * rtt / 1000
    specs = []
    for par in pares:
        for b in bs:
            for rep in range(1, a.reps + 1):
                algos = list(par) if rep % 2 == 1 else list(par[::-1])
                specs.append(dict(serie=f"{par[0]}_vs_{par[1]}", nombre=f"b{b}_rep{rep}", tipo="iperf2",
                                  params=dict(C=C, rtt=rtt, jitter=0, loss=0, buf=int(round(b * bdp)), b=b,
                                              algos=algos, puertos=[5201, 5202], dur=dur, omit=omit,
                                              muestreos=dict(cola_ms=100))))
    return specs


EXPERIMENTOS = {"t1-capacidad": exp_t1_capacidad, "t1-rtt": exp_t1_rtt, "t1-perdida": exp_t1_perdida,
                "t2-ventana": exp_t2_ventana, "t2-libre": exp_t2_libre,
                "t3-uno": exp_t3_uno, "t3-n": exp_t3_n,
                "t4-perdida": exp_t4_perdida,
                "t5-n": exp_t5_n, "t5-rtt": exp_t5_rtt, "t5-algos": exp_t5_algos}
GRUPOS = {"t1": ["t1-capacidad", "t1-rtt", "t1-perdida"], "t2": ["t2-ventana", "t2-libre"],
          "t3": ["t3-uno", "t3-n"], "t4": ["t4-perdida"], "t5": ["t5-n", "t5-rtt", "t5-algos"]}


def duracion_estimada(spec):
    p = spec["params"]
    if spec["tipo"] in ("iperf", "iperf2"):
        return p["dur"] + p["omit"] + 4 + PAUSA_ENTRE_CORRIDAS_S
    return p["n"] * p["intervalo_ms"] / 1000 + 2 + 4 + PAUSA_ENTRE_CORRIDAS_S


# ----------------------------------------------------------------------------- ejecución
def preparar():
    sh(["docker", "compose", "up", "-d", "--wait"], timeout=300)
    _cid.clear()
    for c in ("r_wan", "r_bn"):
        dx(c, "/config/tc.sh", "clear")
    reiniciar_servidores()


def reiniciar_servidores():
    dx("srv", "pkill", "-x", "iperf3", check=False)
    dx("srv", "pkill", "-f", "eco.py", check=False)
    time.sleep(0.5)
    dx("srv", "iperf3", "-s", detach=True)
    dx("srv", "iperf3", "-s", "-p", "5202", detach=True)      # segundo servidor: T5 usa dos clientes a la vez
    dx("srv", "python3", "/config/eco.py", detach=True)
    time.sleep(1)
    for puerto in ("5201", "5202"):
        dx("cli", "nc", "-z", "-w", "2", IP_SRV, puerto)    # falla si iperf3 -s no está escuchando


def configurar_tc(p):
    for c in ("r_wan", "r_bn"):       # clear + configurar: los contadores de tc arrancan en cero
        dx(c, "/config/tc.sh", "clear")
    if not p.get("sin_netem"):
        if p.get("rtts"):       # RTT distinto por flujo (T5b): incluso el control usa esta ruta
            dx("r_wan", "/config/tc5.sh", "rtts", str(p["rtts"][0]), str(p["rtts"][1]),
               str(p["puertos"][0]), str(p["puertos"][1]))
        else:
            dx("r_wan", "/config/tc.sh", "wan", str(p["rtt"]), str(p["jitter"]), str(p["loss"]))
        dx("r_bn", "/config/tc.sh", "bn", str(p["C"]), str(p["buf"]))
    time.sleep(1)


def medir_iperf(p, d):
    cmd = ["iperf3", "-c", IP_SRV, "-R", "-t", str(p["dur"]), "-O", str(p["omit"]),
           "-C", p["algo"], "-J", "-i", "1"]
    if p.get("window_kb"):
        cmd += ["-w", f"{p['window_kb']}K"]
    if p.get("flows", 1) > 1:
        cmd += ["-P", str(p["flows"])]
    r = dx("cli", *cmd, check=False, timeout=p["dur"] + p["omit"] + 60)
    (d / "iperf.json").write_text(r.stdout)
    try:
        j = json.loads(r.stdout)
    except json.JSONDecodeError:
        return ["iperf3 no devolvió JSON: " + (r.stderr or r.stdout)[:200]], {}
    problemas, extra = [], {}
    if "error" in j:
        problemas.append("iperf3 error: " + str(j["error"]))
        return problemas, extra
    fin = j.get("end", {})
    bps = fin.get("sum_received", {}).get("bits_per_second", 0)
    if not bps > 0:
        problemas.append("goodput 0 o ausente")
    cong = fin.get("sender_tcp_congestion")
    extra["congestion_reportada"] = cong
    if cong and cong != p["algo"]:
        problemas.append(f"algoritmo pedido {p['algo']} pero iperf3 reporta {cong}")
    return problemas, extra


def medir_iperf2(p, d):
    """Dos clientes iperf3 a la vez (puertos distintos, algoritmo propio cada uno). Guarda iperf_1.json e iperf_2.json;
    el flujo i usa p['algos'][i], p['puertos'][i] y (en T5b) el RTT p['rtts'][i]."""
    procs = []
    for algo, puerto in zip(p["algos"], p["puertos"]):
        cmd = ["docker", "exec", "cli", "iperf3", "-c", IP_SRV, "-p", str(puerto), "-R", "-t", str(p["dur"]),
               "-O", str(p["omit"]), "-C", algo, "-J", "-i", "1"]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    problemas, extra = [], {}
    for i, (pr, algo) in enumerate(zip(procs, p["algos"]), 1):
        try:
            out, err = pr.communicate(timeout=p["dur"] + p["omit"] + 60)
        except subprocess.TimeoutExpired:
            pr.kill()
            out, err = pr.communicate()
            problemas.append(f"flujo {i}: iperf3 no terminó a tiempo")
        (d / f"iperf_{i}.json").write_text(out)
        try:
            j = json.loads(out)
        except json.JSONDecodeError:
            problemas.append(f"flujo {i}: iperf3 no devolvió JSON: " + (err or out)[:150])
            continue
        if "error" in j:
            problemas.append(f"flujo {i}: iperf3 error: {j['error']}")
            continue
        fin = j.get("end", {})
        if not fin.get("sum_received", {}).get("bits_per_second", 0) > 0:
            problemas.append(f"flujo {i}: goodput 0 o ausente")
        cong = fin.get("sender_tcp_congestion")
        extra[f"congestion_reportada_{i}"] = cong
        if cong and cong != algo:
            problemas.append(f"flujo {i}: algoritmo pedido {algo} pero iperf3 reporta {cong}")
    return problemas, extra


def medir_sonda(p, d):
    cmd = ["python3", "/config/sonda.py", "--dest", IP_SRV, "--n", str(p["n"]),
           "--intervalo-ms", str(p["intervalo_ms"])]
    r = dx("cli", *cmd, check=False, timeout=p["n"] * p["intervalo_ms"] / 1000 + 60)
    (d / "sonda.csv").write_text(r.stdout)
    filas = max(r.stdout.count("\n") - 1, 0)
    problemas = [] if filas == p["n"] else [f"sonda: {filas} filas, se esperaban {p['n']}"]
    return problemas, {"sonda_stderr": r.stderr.strip()[-300:]}


def estado_sysctl():
    """Topes de buffer de socket vistos desde los contenedores (recortan lo que pide iperf3 -w)."""
    out = {}
    for cont, clave, ruta in (("srv", "wmem_max", "/proc/sys/net/core/wmem_max"),
                              ("cli", "rmem_max", "/proc/sys/net/core/rmem_max")):
        r = dx(cont, "cat", ruta, check=False)
        out[clave] = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
    return out


def iniciar_muestreos(p):
    """Lanza muestreadores en segundo plano dentro de los contenedores. Devuelve [(cont, nombre, ruta)]."""
    mu, lista = p.get("muestreos", {}), []
    pedidos = []
    if "ss_ms" in mu:   # socket del emisor (srv, por iperf3 -R): cwnd, srtt, ventana efectiva, sndbuf
        for i, puerto in enumerate(mu.get("ss_puertos", [5201])):
            nombre = "ss" if "ss_puertos" not in mu else f"ss{i + 1}"
            pedidos.append(("srv", nombre, ["ss", "-tinm", "state", "established", "sport", "=", f":{puerto}"],
                            mu["ss_ms"]))
    if "cola_ms" in mu:  # ocupación de la cola del tbf
        ifbn = dx("r_bn", "/config/tc.sh", "ifbn").stdout.strip()
        pedidos.append(("r_bn", "cola", ["tc", "-s", "-j", "qdisc", "show", "dev", ifbn], mu["cola_ms"]))
    if "sst_ms" in mu:  # un registro compacto por socket (ventana de cada flujo): sincronización en T3
        pedidos.append(("srv", "sst", None, mu["sst_ms"]))
    for cont, nombre, cmd, ms in pedidos:
        ruta = f"/tmp/{nombre}.jsonl"
        dx(cont, "rm", "-f", ruta, check=False)
        if nombre == "sst":
            dx(cont, "python3", "/config/muestreo_ss.py", "--intervalo-ms", str(ms), "--salida", ruta, detach=True)
        else:
            dx(cont, "python3", "/config/muestreo.py", "--intervalo-ms", str(ms), "--salida", ruta, "--", *cmd,
               detach=True)
        lista.append((cont, nombre, ruta))
    time.sleep(0.5)
    return lista


def detener_muestreos(lista, d):
    """Detiene los muestreadores, trae sus archivos y devuelve avisos si no capturaron lo esperado."""
    avisos = []
    for cont, nombre, ruta in lista:
        dx(cont, "pkill", "-f", f"salida {ruta}", check=False)
        r = dx(cont, "cat", ruta, check=False)
        (d / f"{nombre}.jsonl").write_text(r.stdout)
        token = '"f":[[' if nombre == "sst" else ("cwnd:" if nombre.startswith("ss") else ("tbf" if nombre == "cola" else None))
        if token and token not in r.stdout:
            avisos.append(f"muestreo '{nombre}' sin datos válidos (revisar {nombre}.jsonl)")
    return avisos


def leer_netstat():
    """Contadores TCP del kernel (netns del servidor): /proc/net/netstat (TcpExt: timeouts, TLP, recuperaciones...)
    y /proc/net/snmp (retransmisiones). Se guardan crudos antes y después de cada corrida."""
    partes = [f"=== t ===\n{time.time():.3f}"]      # instante de la foto (epoch del host)
    for nombre in ("netstat", "snmp"):
        r = dx("srv", "cat", f"/proc/net/{nombre}", check=False)
        partes.append(f"=== {nombre} ===\n{r.stdout}")
    return "\n".join(partes)


def delta_cpu(antes, despues):
    out = {}
    for c in CONTENEDORES:
        if antes.get(c) and despues.get(c):
            out[c] = {k: despues[c][k] - antes[c][k] for k in ("usage_usec", "nr_throttled")
                      if k in despues[c] and k in antes[c]}
    return out


def correr_una(exp, spec, raiz_res):
    p = spec["params"]
    d = raiz_res / exp / spec["serie"] / spec["nombre"]
    d.mkdir(parents=True, exist_ok=True)
    meta = dict(exp=exp, serie=spec["serie"], corrida=spec["nombre"], tipo=spec["tipo"], params=p,
                semilla=semilla(exp, spec["serie"], spec["nombre"]),
                inicio=datetime.now().isoformat(timespec="seconds"),
                kernel=platform.release(), status="en_curso")
    t0 = time.time()
    try:
        configurar_tc(p)
        antes = {c: cpu_stat(c) for c in CONTENEDORES}
        meta.update(estado_cpu_host())
        meta.update(estado_sysctl())
        muestreos = iniciar_muestreos(p)
        t_med = time.time()
        meta["t_iperf_inicio"] = t_med      # epoch del host: sirve para alinear las muestras de ss y de la cola
        avisos_m = []
        hilo_ns = None
        if p.get("netstat"):
            (d / "netstat_antes.txt").write_text(leer_netstat())

            def _foto_ventana():
                # al inicio de la ventana de medición (omit + 1.5 s): lo anterior es arranque (slow start, etc.)
                espera = t_med + p["omit"] + 1.5 - time.time()
                if espera > 0:
                    time.sleep(espera)
                (d / "netstat_ventana.txt").write_text(leer_netstat())
            hilo_ns = threading.Thread(target=_foto_ventana, daemon=True)
            hilo_ns.start()
        medir = {"iperf": medir_iperf, "iperf2": medir_iperf2, "sonda": medir_sonda}[spec["tipo"]]
        try:
            with MuestreadorFreq(cpus_medidos()) as mf:
                problemas, extra = medir(p, d)
        finally:
            avisos_m = detener_muestreos(muestreos, d)
        if p.get("netstat"):
            if hilo_ns is not None:
                hilo_ns.join(timeout=10)
            (d / "netstat_despues.txt").write_text(leer_netstat())
        if avisos_m:
            meta["advertencias"] = avisos_m
        meta["duracion_medicion_s"] = round(time.time() - t_med, 2)
        meta["freq_khz_medicion"] = mf.resumen()
        despues = {c: cpu_stat(c) for c in CONTENEDORES}
        meta["cpu_cgroup"] = dict(antes=antes, despues=despues, delta=delta_cpu(antes, despues))
        (d / "tc_wan.txt").write_text(dx("r_wan", "/config/tc.sh", "show", check=False).stdout)
        (d / "tc_bn.txt").write_text(dx("r_bn", "/config/tc.sh", "show", check=False).stdout)
        meta.update(extra)
        meta["problemas"] = problemas
        meta["status"] = "ok" if not problemas else "fallida"
    except Exception as e:
        meta["status"] = "error"
        meta["error"] = traceback.format_exc()
        meta["error_resumen"] = f"{type(e).__name__}: {str(e)[:300]}"
        try:
            reiniciar_servidores()
        except Exception:
            pass
    meta["duracion_total_s"] = round(time.time() - t0, 2)
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta["status"], meta


def ya_hecha(exp, spec, raiz_res):
    m = raiz_res / exp / spec["serie"] / spec["nombre"] / "meta.json"
    if not m.exists():
        return False
    try:
        return json.loads(m.read_text()).get("status") == "ok"
    except json.JSONDecodeError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exp", choices=list(EXPERIMENTOS) + list(GRUPOS))
    ap.add_argument("--reps", type=int, default=3, help="repeticiones por punto (default 3)")
    ap.add_argument("--rapido", action="store_true", help="prueba corta; escribe en results-prueba/")
    ap.add_argument("--listar", action="store_true", help="solo lista corridas y tiempo estimado")
    ap.add_argument("--sin-bbr", action="store_true", help="omite BBR (opcional en el enunciado)")
    a = ap.parse_args()
    if a.rapido:
        a.reps = 1
    raiz_res = RAIZ / ("results-prueba" if a.rapido else "results")
    exps = GRUPOS.get(a.exp, [a.exp])

    plan = []
    for exp in exps:
        specs = EXPERIMENTOS[exp](a)
        random.Random(zlib.crc32(exp.encode())).shuffle(specs)   # orden aleatorio, reproducible
        plan += [(exp, s) for s in specs]

    pendientes = [(e, s) for e, s in plan if not ya_hecha(e, s, raiz_res)]
    est = sum(duracion_estimada(s) for _, s in pendientes)
    print(f"{len(plan)} corridas en total, {len(pendientes)} pendientes, "
          f"tiempo estimado ~{est/60:.0f} min  (salida en {raiz_res.name}/)")
    if a.listar:
        for i, (e, s) in enumerate(plan, 1):
            marca = "hecha" if (e, s) not in pendientes else "     "
            print(f"  {i:3d} {marca} {e}/{s['serie']}/{s['nombre']}  ~{duracion_estimada(s):.0f} s")
        return

    try:                                    # el runner vive en el núcleo del host
        os.sched_setaffinity(0, {int(leer_env()["CPU_HOST"])})
    except (KeyError, ValueError, OSError):
        pass
    preparar()
    restante = est
    for i, (exp, spec) in enumerate(pendientes, 1):
        t = time.time()
        estado, meta = correr_una(exp, spec, raiz_res)
        restante -= duracion_estimada(spec)
        extra = ("" if estado == "ok" else "  -> " + (meta.get("error_resumen", "") or "; ".join(meta.get("problemas", [])))[:300])
        if meta.get("advertencias"):
            extra += "  AVISO: " + "; ".join(meta["advertencias"])
        print(f"[{i}/{len(pendientes)}] {exp}/{spec['serie']}/{spec['nombre']}  {estado}  "
              f"{time.time()-t:.0f} s  (faltan ~{max(restante,0)/60:.0f} min){extra}", flush=True)
        time.sleep(PAUSA_ENTRE_CORRIDAS_S)
    for c in ("r_wan", "r_bn"):
        dx(c, "/config/tc.sh", "clear", check=False)
    fallidas = sum(1 for e, s in plan if not ya_hecha(e, s, raiz_res))
    print(f"Listo. Corridas sin status ok: {fallidas} (volver a lanzar el mismo comando las reintenta).")


if __name__ == "__main__":
    main()
