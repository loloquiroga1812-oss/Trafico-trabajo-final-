import subprocess
import json
import time
import zlib
import itertools
from datetime import datetime
from pathlib import Path
import numpy as np

EXPERIMENTO = "H4"
DIR_BASE = Path("results") / EXPERIMENTO
URL_API1 = "http://198.18.3.11:8000/t"
IP_API1 = "198.18.3.11"
TASA_ENLACE_MBPS = 25.0
C_BYTES_S = TASA_ENLACE_MBPS * 1e6 / 8.0   # capacidad del enlace en bytes/s
BURST_TBF = "16kb"                         # antes 32kb (ver README/informe)
MSS = 1448.0                               # payload TCP por trama
HDR_TRAMA = 66.0                           # Eth14 + IP20 + TCP32 (con timestamps)


def ejecutar(cmd, ignorar_error=False):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not ignorar_error:
        raise RuntimeError(f"Fallo: {cmd}\n{res.stderr.strip()}")
    return res.stdout


def obtener_if(cont, ip_obj):
    out = ejecutar(f"docker exec {cont} ip -o -4 addr show")
    for linea in out.splitlines():
        if ip_obj in linea:
            return linea.split()[1]
    raise RuntimeError(f"Sin interfaz {ip_obj} en {cont}")


def configurar_red_h4():
    if_bn = obtener_if("r_bn", "198.18.2.254")
    if_acc = obtener_if("r_wan", "198.18.1.254")
    if_core = obtener_if("r_wan", "198.18.2.253")

    # Limpiar r_wan (sin retardo extra) y fijar tbf 25 Mbit/s en r_bn
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_acc} root", True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_core} root", True)
    ejecutar(
        f"docker exec r_bn tc qdisc replace dev {if_bn} root tbf "
        f"rate {TASA_ENLACE_MBPS:.0f}mbit burst {BURST_TBF} limit 2mb"
    )
    return if_bn


def limpiar_tc():
    if_bn = obtener_if("r_bn", "198.18.2.254")
    if_acc = obtener_if("r_wan", "198.18.1.254")
    if_core = obtener_if("r_wan", "198.18.2.253")
    ejecutar(f"docker exec r_bn tc qdisc del dev {if_bn} root", True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_acc} root", True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_core} root", True)


def leer_stats_tc(if_bn):
    out = ejecutar(f"docker exec r_bn tc -s qdisc show dev {if_bn}", True)
    bytes_env = 0
    pkts_env = 0
    drops = 0
    for linea in out.splitlines():
        partes = linea.strip().split()
        if "Sent" in partes:
            idx = partes.index("Sent")
            bytes_env = int(partes[idx + 1])
            pkts_env = int(partes[idx + 3])
        if "(dropped" in partes:
            idx = partes.index("(dropped")
            drops = int(partes[idx + 1].rstrip(","))
    return {"bytes": bytes_env, "pkts": pkts_env, "dropped": drops}


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


def detener_uvicorn():
    kill_py = (
        "import os, signal; "
        "[os.kill(int(p), signal.SIGKILL) for p in os.listdir('/proc') "
        "if p.isdigit() and int(p) != os.getpid() "
        "and b'uvicorn' in open(f'/proc/{p}/cmdline', 'rb').read()]"
    )
    ejecutar(f'docker exec api1 python3 -c "{kill_py}"', True)


def iniciar_servicio_fifo():
    ejecutar("docker update --cpu-period=10000 --cpu-quota=-1 api1")
    detener_uvicorn()
    time.sleep(0.3)
    cmd = (
        "docker exec -d -e MODO_ATENCION=FIFO api1 "
        "sh -c 'uvicorn --app-dir /app servicio:app "
        "--host 0.0.0.0 --port 8000 > /tmp/uvicorn_h4.log 2>&1'"
    )
    ejecutar(cmd)

    py_check = (
        "import urllib.request; "
        "r=urllib.request.urlopen("
        "'http://198.18.3.11:8000/t?d=0&r=1', timeout=1); "
        "print(r.status)"
    )
    for _ in range(30):
        out = ejecutar(f'docker exec gen python3 -c "{py_check}"', True)
        if "200" in out:
            return
        time.sleep(0.2)
    log_api = ejecutar("docker exec api1 cat /tmp/uvicorn_h4.log", True)
    raise RuntimeError(f"api1 no respondio en modo FIFO.\n{log_api}")


def medir_sonda(nombre_csv, r_bytes, n_ped=200):
    f_sonda = DIR_BASE / nombre_csv
    cmd = (
        f"docker exec gen python3 /app/generador.py "
        f"--url {URL_API1} --lazo cerrado --pedidos {n_ped} "
        f"--d-media 0.0 --dist-d det --r-media {r_bytes} --dist-r det "
        f"--semilla 2026 --salida /results/{EXPERIMENTO}/{nombre_csv}"
    )
    ejecutar(cmd)
    lineas = f_sonda.read_text().splitlines()
    enc = lineas[0].split(",")
    idx_lat = enc.index("latencia_total_ms")
    idx_han = enc.index("t_handler_ms")
    lats, hans = [], []
    for ln in lineas[1:]:
        cols = ln.split(",")
        lats.append(float(cols[idx_lat]))
        hans.append(float(cols[idx_han]))
    return np.asarray(lats, dtype=float), np.asarray(hans, dtype=float)


def calibrar_tandem(if_bn):
    """Calibra: s0_cpu, H (overhead HTTP+ACKs en cable por pedido) y c0.

    El servicio del enlace NO se calibra con tiempos (dependia del burst del
    tbf), sino con bytes reales en cable: S_red = bytes_cable / C.
    """
    f_cal = DIR_BASE / "calibracion_tandem.json"
    if f_cal.exists():
        return json.loads(f_cal.read_text())

    print("Calibrando CPU base (s0) y overhead en cable...")
    r1 = 100
    r2 = 30000

    tc_a = leer_stats_tc(if_bn)
    lat1, han1 = medir_sonda("sonda_r100.csv", r1)
    tc_b = leer_stats_tc(if_bn)
    lat2, _ = medir_sonda("sonda_r30000.csv", r2)
    tc_c = leer_stats_tc(if_bn)

    n1, n2 = len(lat1), len(lat2)
    wire1 = (tc_b["bytes"] - tc_a["bytes"]) / n1   # B en cable por pedido (R=100)
    wire2 = (tc_c["bytes"] - tc_b["bytes"]) / n2   # B en cable por pedido (R=30000)

    # R=100 + H cabe en una sola trama: wire1 = (R+H) + 66  ->  H
    h_http = max(0.0, wire1 - r1 - HDR_TRAMA)
    # chequeo: pendiente de bytes en cable vs R, teorica = 1514/1448
    pend_wire = (wire2 - wire1) / float(r2 - r1)

    s0_ms = float(np.mean(han1))
    t1_ms = float(np.mean(lat1))
    t2_ms = float(np.mean(lat2))
    ms_por_byte = (t2_ms - t1_ms) / float(r2 - r1)  # solo diagnostico
    s_red1_ms = wire1 / C_BYTES_S * 1000.0
    c0_puro_ms = max(0.1, t1_ms - s0_ms - s_red1_ms)

    cal = {
        "hora": datetime.now().isoformat(),
        "tasa_enlace_mbps": TASA_ENLACE_MBPS,
        "burst_tbf": BURST_TBF,
        "s0_cpu_ms": s0_ms,
        "c0_puro_ms": c0_puro_ms,
        "h_http_bytes": h_http,
        "wire_r100_bytes": wire1,
        "wire_r30000_bytes": wire2,
        "pendiente_wire_medida": pend_wire,
        "pendiente_wire_teorica": (MSS + HDR_TRAMA) / MSS,
        "ms_por_byte_payload_diag": ms_por_byte,
        "ms_por_byte_teorico": 1000.0 / C_BYTES_S * (MSS + HDR_TRAMA) / MSS,
    }
    f_cal.write_text(json.dumps(cal, indent=4))
    print(
        f"Calibracion lista -> s0_cpu={s0_ms:.4f} ms | H={h_http:.0f} B | "
        f"pend_wire={pend_wire:.4f} (teo {cal['pendiente_wire_teorica']:.4f}) | "
        f"c0={c0_puro_ms:.4f} ms"
    )
    return cal


def es_valida(d_cor, tasa_esp, dur_util):
    f_meta = d_cor / "meta.json"
    f_csv = d_cor / "pedidos.csv"
    if not f_meta.exists() or not f_csv.exists():
        return False
    try:
        meta = json.loads(f_meta.read_text())
        min_ped = int(tasa_esp * dur_util * 0.75)
        return meta.get("pedidos_ok", 0) >= max(50, min_ped)
    except Exception:
        return False


def main():
    print(f"Iniciando {EXPERIMENTO} (Tandem CPU -> Enlace {TASA_ENLACE_MBPS:.0f} Mbit/s)")
    DIR_BASE.mkdir(parents=True, exist_ok=True)

    conts = ["gen", "r_wan", "r_bn", "api1"]
    cgroups = {c: ruta_cgroup(c) for c in conts}

    e_d_ms = 10.0
    combos = [(0.5, 0.5), (0.5, 0.7), (0.7, 0.5), (0.7, 0.7)]
    reps = [1, 2, 3]
    duracion_s = 130.0
    calentamiento_s = 10.0
    dur_util_s = duracion_s - calentamiento_s

    corridas = [
        (rc, rr, rep)
        for (rc, rr), rep in itertools.product(combos, reps)
    ]
    sem_global = zlib.crc32(EXPERIMENTO.encode("utf-8"))
    rng = np.random.default_rng(sem_global)
    rng.shuffle(corridas)
    total = len(corridas)

    try:
        if_bn = configurar_red_h4()
        iniciar_servicio_fifo()
        cal = calibrar_tandem(if_bn)

        s0_cpu_ms = cal["s0_cpu_ms"]
        h_http = cal["h_http_bytes"]
        e_s_cpu_s = (e_d_ms + s0_cpu_ms) / 1000.0
        # parte fija de bytes en cable por pedido (promedio sobre R exponencial):
        # E[wire] = E[R]*1514/1448 + H*1514/1448 + 33  (33 = media trama parcial)
        w0 = h_http * (MSS + HDR_TRAMA) / MSS + HDR_TRAMA / 2.0

        for idx, (rho_cpu, rho_red, rep) in enumerate(corridas, start=1):
            c_str = str(rho_cpu).replace(".", "")
            r_str = str(rho_red).replace(".", "")
            nombre = f"TANDEM_cpu{c_str}_red{r_str}_rep{rep}"
            d_cor = DIR_BASE / nombre

            tasa = rho_cpu / e_s_cpu_s
            # rho_red = lambda * E[wire] / C  ->  E[wire] objetivo
            e_wire_obj = (rho_red / tasa) * C_BYTES_S
            r_media_bytes = max(
                500, int(round((e_wire_obj - w0) * MSS / (MSS + HDR_TRAMA)))
            )

            if es_valida(d_cor, tasa, dur_util_s):
                print(f"[{idx}/{total}] {nombre}: ok, salteando...")
                continue

            sem = zlib.crc32(f"{EXPERIMENTO}_{nombre}".encode("utf-8"))
            print(
                f"[{idx}/{total}] {nombre} (rho_cpu={rho_cpu}, "
                f"rho_red={rho_red}, lambda={tasa:.2f} req/s, "
                f"E[R]={r_media_bytes} B)..."
            )
            d_cor.mkdir(parents=True, exist_ok=True)

            try:
                d_seg = e_d_ms / 1000.0
                cpu0 = {c: leer_cpu(r) for c, r in cgroups.items()}
                tc0 = leer_stats_tc(if_bn)
                t0 = time.perf_counter()

                ruta_out = f"/results/{EXPERIMENTO}/{nombre}/pedidos.csv"
                cmd_gen = (
                    f"docker exec gen python3 /app/generador.py "
                    f"--url {URL_API1} --lazo abierto "
                    f"--duracion {duracion_s:.1f} "
                    f"--calentamiento {calentamiento_s:.1f} "
                    f"--tasa {tasa:.6f} --d-media {d_seg:.6f} "
                    f"--dist-d exp --r-media {r_media_bytes} --dist-r exp "
                    f"--semilla {sem} --salida {ruta_out}"
                )
                ejecutar(cmd_gen)

                dur_real = time.perf_counter() - t0
                tc1 = leer_stats_tc(if_bn)
                cpu1 = {c: leer_cpu(r) for c, r in cgroups.items()}

                cpu_d = {}
                for c in cgroups:
                    if cpu0[c] and cpu1[c]:
                        u0 = cpu0[c].get("usage_usec", 0)
                        u1 = cpu1[c].get("usage_usec", 0)
                        t0_th = cpu0[c].get("nr_throttled", 0)
                        t1_th = cpu1[c].get("nr_throttled", 0)
                        cpu_d[c] = {
                            "usage_usec": u1 - u0,
                            "nr_throttled": t1_th - t0_th,
                        }

                tc_delta = {
                    "bytes_enviados": tc1["bytes"] - tc0["bytes"],
                    "pkts_enviados": tc1["pkts"] - tc0["pkts"],
                    "dropped": tc1["dropped"] - tc0["dropped"],
                }

                f_csv = d_cor / "pedidos.csv"
                n_ok = max(0, len(f_csv.read_text().splitlines()) - 1)

                meta = {
                    "experimento": EXPERIMENTO,
                    "corrida": nombre,
                    "semilla": sem,
                    "hora": datetime.now().isoformat(),
                    "calibracion_tandem": cal,
                    "parametros": {
                        "modo": "FIFO",
                        "tasa_enlace_mbps": TASA_ENLACE_MBPS,
                        "burst_tbf": BURST_TBF,
                        "e_d_ms": e_d_ms,
                        "r_media_bytes": r_media_bytes,
                        "e_wire_obj_bytes": e_wire_obj,
                        "rho_cpu_objetivo": rho_cpu,
                        "rho_red_objetivo": rho_red,
                        "tasa_objetivo_rps": tasa,
                        "duracion_s": duracion_s,
                        "calentamiento_s": calentamiento_s,
                    },
                    "repeticion": rep,
                    "pedidos_ok": n_ok,
                    "duracion_real_s": dur_real,
                    "tc_delta": tc_delta,
                    "cpu_cgroup_delta": cpu_d,
                }
                (d_cor / "meta.json").write_text(json.dumps(meta, indent=4))

            except Exception as e:
                print(f"[{idx}/{total}] ERROR en {nombre}: {e}")

    finally:
        detener_uvicorn()
        limpiar_tc()
        print("\nExperimento H4 finalizado.")


if __name__ == "__main__":
    main()
