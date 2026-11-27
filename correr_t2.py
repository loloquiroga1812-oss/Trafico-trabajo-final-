import subprocess
import json
import time
import zlib
import itertools
import threading
from datetime import datetime
from pathlib import Path
import numpy as np

EXPERIMENTO = "T2"
DIR_BASE = Path("results") / EXPERIMENTO
CAPACIDAD_MBPS = 100.0
CAPACIDAD_BPS = CAPACIDAD_MBPS * 1e6  # 100 Mbit/s = 12.5 MB/s


def ejecutar(cmd, ignorar_error=False):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not ignorar_error:
        raise RuntimeError(f"Fallo: {cmd}\n{res.stderr.strip()}")
    return res.stdout


def detectar_interfaces():
    def mapear(cont):
        out = ejecutar(f"docker exec {cont} ip -o -4 addr show")
        mapa = {}
        for ln in out.splitlines():
            p = ln.split()
            if len(p) >= 4:
                iface = p[1]
                ip = p[3].split("/")[0]
                mapa[ip] = iface
        return mapa

    m_cli = mapear("cli")
    m_wan = mapear("r_wan")
    m_bn = mapear("r_bn")
    m_srv = mapear("srv")

    return {
        "cli_acc": m_cli["198.18.1.10"],
        "wan_acc": m_wan["198.18.1.254"],
        "wan_core": m_wan["198.18.2.253"],
        "bn_core": m_bn["198.18.2.254"],
        "bn_srv": m_bn["198.18.3.254"],
        "srv_srv": m_srv["198.18.3.10"],
    }


def configurar_red_base(ifaces):
    print("Configurando rutas estaticas, desactivando offloads y ajustando sysctls...")
    for cont in ["cli", "r_wan", "r_bn", "srv"]:
        ejecutar(f"docker exec {cont} ip route del default", True)

    ejecutar(f"docker exec cli ip route replace 198.18.0.0/15 via 198.18.1.254")
    ejecutar(f"docker exec r_wan ip route replace 198.18.3.0/24 via 198.18.2.254")
    ejecutar(f"docker exec r_bn ip route replace 198.18.1.0/24 via 198.18.2.253")
    ejecutar(f"docker exec srv ip route replace 198.18.0.0/15 via 198.18.3.254")

    pares_if = [
        ("cli", ifaces["cli_acc"]),
        ("r_wan", ifaces["wan_acc"]),
        ("r_wan", ifaces["wan_core"]),
        ("r_bn", ifaces["bn_core"]),
        ("r_bn", ifaces["bn_srv"]),
        ("srv", ifaces["srv_srv"]),
    ]
    for cont, iface in pares_if:
        ejecutar(
            f"docker exec {cont} ethtool -K {iface} gro off gso off tso off",
            True,
        )

    # Evitar que Linux reutilice ssthresh/rtt cacheados entre corridas
    for cont in ["cli", "srv"]:
        ejecutar(f"docker exec {cont} sysctl -w net.ipv4.tcp_no_metrics_save=1", True)


def algoritmos_disponibles():
    out = ejecutar("docker exec srv sysctl -n net.ipv4.tcp_available_congestion_control")
    disp = out.strip().split()
    algos = [a for a in ["reno", "cubic", "bbr"] if a in disp]
    if not algos:
        algos = ["reno", "cubic"]
    return algos


def iniciar_servidor_iperf3():
    ejecutar("docker exec srv pkill -9 iperf3", True)
    time.sleep(0.2)
    ejecutar("docker exec -d srv iperf3 -s")
    time.sleep(0.3)


def configurar_red_experimento(ifaces, rtt_ms, buffer_bytes):
    # 1. RTT/2 en cada sentido en r_wan
    for iface in [ifaces["wan_acc"], ifaces["wan_core"]]:
        ejecutar(f"docker exec r_wan tc qdisc del dev {iface} root", True)
        if rtt_ms > 0:
            half_ms = rtt_ms / 2.0
            ejecutar(
                f"docker exec r_wan tc qdisc replace dev {iface} root "
                f"netem delay {half_ms:.3f}ms limit 20000"
            )

    # 2. Cuello de botella TBF en salida de r_bn hacia cli (bn_core)
    if_bn = ifaces["bn_core"]
    ejecutar(f"docker exec r_bn tc qdisc del dev {if_bn} root", True)
    burst_b = 16000
    limit_b = max(int(buffer_bytes), burst_b + 4096)
    ejecutar(
        f"docker exec r_bn tc qdisc replace dev {if_bn} root "
        f"tbf rate {CAPACIDAD_MBPS:.0f}mbit burst {burst_b} limit {limit_b}"
    )


def medir_rtt_base(ifaces):
    configurar_red_experimento(ifaces, rtt_ms=0, buffer_bytes=2_000_000)
    out = ejecutar("docker exec cli ping -c 30 -i 0.05 198.18.3.10")
    for ln in out.splitlines():
        if "rtt min/avg/max" in ln or "round-trip min/avg/max" in ln:
            partes = ln.split("=")[1].strip().split("/")
            return float(partes[1])
    return 0.0


def ruta_cgroup(cont):
    cid = ejecutar(f"docker inspect --format '{{{{.Id}}}}' {cont}").strip()
    for r in [
        Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat"),
        Path(f"/sys/fs/cgroup/docker/{cid}/cpu.stat"),
    ]:
        if r.exists():
            return r
    return None


def leer_cpu(ruta):
    if ruta is None or not ruta.exists():
        return {}
    datos = {}
    for ln in ruta.read_text().splitlines():
        p = ln.split()
        if len(p) == 2:
            datos[p[0]] = int(p[1])
    return datos


def muestrear_cola_tbf(if_bn, evento_fin, muestras):
    t_ini = time.perf_counter()
    while not evento_fin.is_set():
        out = ejecutar(f"docker exec r_bn tc -s -j qdisc show dev {if_bn}", True)
        t_rel = time.perf_counter() - t_ini
        try:
            arr = json.loads(out)
            tbf = next((x for x in arr if x.get("kind") == "tbf"), None)
            if tbf:
                muestras.append({
                    "t_s": round(t_rel, 3),
                    "backlog_bytes": int(tbf.get("backlog", 0)),
                    "qlen_pkts": int(tbf.get("qlen", 0)),
                    "drops": int(tbf.get("drops", 0)),
                })
        except Exception:
            pass
        evento_fin.wait(0.1)


def es_valida(d_cor, pedir_cola=False):
    f_meta = d_cor / "meta.json"
    f_ip = d_cor / "iperf.json"
    if not f_meta.exists() or not f_ip.exists():
        return False
    if pedir_cola and not (d_cor / "cola.json").exists():
        return False
    try:
        data = json.loads(f_ip.read_text())
        if "error" in data:
            return False
        ivs = data.get("intervals", [])
        srv_ivs = data.get("server_output_json", {}).get("intervals", [])
        return len(ivs) >= 4 and len(srv_ivs) >= 4
    except Exception:
        return False


def main():
    print(f"Iniciando {EXPERIMENTO} (Ventana y BDP)")
    DIR_BASE.mkdir(parents=True, exist_ok=True)
    (DIR_BASE / "fija").mkdir(exist_ok=True)
    (DIR_BASE / "libre_bdp").mkdir(exist_ok=True)

    ifaces = detectar_interfaces()
    configurar_red_base(ifaces)
    algos = algoritmos_disponibles()
    print(f"Algoritmos disponibles: {algos}")

    iniciar_servidor_iperf3()
    rtt_base_ms = medir_rtt_base(ifaces)
    print(f"RTT base medido sin netem: {rtt_base_ms:.4f} ms")

    conts = ["cli", "r_wan", "r_bn", "srv"]
    cgroups = {c: ruta_cgroup(c) for c in conts}

    # --- Construir lista de corridas ---
    ventanas_kb = [32, 64, 128, 256, 512, 1024]
    rtts_fija = [10, 20, 50, 100, 200]
    rtts_libre = [20, 50, 100]
    reps = [1, 2, 3]

    tareas = []
    # Bloque 1: Ventana fija (iperf3 -w)
    for w_kb, rtt_ms, algo, rep in itertools.product(ventanas_kb, rtts_fija, algos, reps):
        tareas.append({
            "tipo": "fija",
            "nombre": f"W{w_kb}_RTT{rtt_ms}_{algo}_rep{rep}",
            "w_kb": w_kb,
            "rtt_ms": rtt_ms,
            "algo": algo,
            "rep": rep,
            "duracion_s": 10,
            "omit_s": 2,
            "buffer_bytes": 2_500_000,  # holgado para que no caigan paquetes por buffer
        })

    # Bloque 2: Ventana libre con buffer = BDP
    for rtt_ms, algo, rep in itertools.product(rtts_libre, algos, reps):
        bdp_bytes = int((CAPACIDAD_BPS / 8.0) * (rtt_ms / 1000.0))
        tareas.append({
            "tipo": "libre_bdp",
            "nombre": f"RTT{rtt_ms}_{algo}_rep{rep}",
            "w_kb": None,
            "rtt_ms": rtt_ms,
            "algo": algo,
            "rep": rep,
            "duracion_s": 18,
            "omit_s": 2,
            "buffer_bytes": bdp_bytes,
        })

    sem_global = zlib.crc32(EXPERIMENTO.encode("utf-8"))
    rng = np.random.default_rng(sem_global)
    rng.shuffle(tareas)
    total = len(tareas)

    try:
        for idx, t in enumerate(tareas, start=1):
            d_cor = DIR_BASE / t["tipo"] / t["nombre"]
            pedir_cola = t["tipo"] == "libre_bdp"

            if es_valida(d_cor, pedir_cola=pedir_cola):
                print(f"[{idx}/{total}] {t['tipo']}/{t['nombre']}: ok, salteando...")
                continue

            d_cor.mkdir(parents=True, exist_ok=True)
            print(
                f"[{idx}/{total}] {t['tipo']}/{t['nombre']} "
                f"(RTT={t['rtt_ms']}ms, W={t['w_kb']}kB, B={t['buffer_bytes']}B)..."
            )

            try:
                configurar_red_experimento(ifaces, t["rtt_ms"], t["buffer_bytes"])
                cpu0 = {c: leer_cpu(r) for c, r in cgroups.items()}

                ev_fin = threading.Event()
                muestras_cola = []
                hilo_cola = None
                if pedir_cola:
                    hilo_cola = threading.Thread(
                        target=muestrear_cola_tbf,
                        args=(ifaces["bn_core"], ev_fin, muestras_cola),
                        daemon=True,
                    )
                    hilo_cola.start()

                w_flag = f"-w {t['w_kb']}K " if t["w_kb"] is not None else ""
                cmd_iperf = (
                    f"docker exec cli iperf3 -c 198.18.3.10 -R "
                    f"-C {t['algo']} {w_flag}"
                    f"-t {t['duracion_s']} -O {t['omit_s']} "
                    f"--get-server-output -J"
                )
                t0 = time.perf_counter()
                out_json = ejecutar(cmd_iperf)
                dur_real = time.perf_counter() - t0

                if hilo_cola is not None:
                    ev_fin.set()
                    hilo_cola.join(timeout=1.0)
                    (d_cor / "cola.json").write_text(json.dumps(muestras_cola, indent=2))

                cpu1 = {c: leer_cpu(r) for c, r in cgroups.items()}
                (d_cor / "iperf.json").write_text(out_json)

                cpu_d = {}
                for c in cgroups:
                    if cpu0[c] and cpu1[c]:
                        cpu_d[c] = {
                            "usage_usec": cpu1[c].get("usage_usec", 0) - cpu0[c].get("usage_usec", 0),
                            "nr_throttled": cpu1[c].get("nr_throttled", 0) - cpu0[c].get("nr_throttled", 0),
                        }

                meta = {
                    "experimento": EXPERIMENTO,
                    "tipo": t["tipo"],
                    "corrida": t["nombre"],
                    "hora": datetime.now().isoformat(),
                    "rtt_base_ms": rtt_base_ms,
                    "parametros": {
                        "capacidad_mbps": CAPACIDAD_MBPS,
                        "w_kb": t["w_kb"],
                        "rtt_cfg_ms": t["rtt_ms"],
                        "algoritmo": t["algo"],
                        "repeticion": t["rep"],
                        "duracion_s": t["duracion_s"],
                        "omit_s": t["omit_s"],
                        "buffer_bytes": t["buffer_bytes"],
                    },
                    "duracion_real_s": dur_real,
                    "cpu_cgroup_delta": cpu_d,
                }
                (d_cor / "meta.json").write_text(json.dumps(meta, indent=4))

            except Exception as e:
                print(f"[{idx}/{total}] ERROR en {t['nombre']}: {e}")
                iniciar_servidor_iperf3()

    finally:
        ejecutar("docker exec srv pkill -9 iperf3", True)
        for iface in [ifaces["wan_acc"], ifaces["wan_core"]]:
            ejecutar(f"docker exec r_wan tc qdisc del dev {iface} root", True)
        ejecutar(f"docker exec r_bn tc qdisc del dev {ifaces['bn_core']} root", True)
        print("\nExperimento T2 finalizado.")


if __name__ == "__main__":
    main()
