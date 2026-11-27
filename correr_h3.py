import subprocess
import json
import time
import zlib
import itertools
from datetime import datetime
from pathlib import Path
import numpy as np

EXPERIMENTO = "H3"
DIR_BASE = Path("results") / EXPERIMENTO
URL_API1 = "http://198.18.3.11:8000/t"
IP_API1 = "198.18.3.11"


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


def limpiar_tc():
    if_bn = obtener_if("r_bn", "198.18.2.254")
    if_acc = obtener_if("r_wan", "198.18.1.254")
    if_core = obtener_if("r_wan", "198.18.2.253")
    ejecutar(f"docker exec r_bn tc qdisc del dev {if_bn} root", True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_acc} root", True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_core} root", True)


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


def iniciar_servicio_ps():
    ejecutar("docker update --cpu-period=10000 --cpu-quota=-1 api1")
    detener_uvicorn()
    time.sleep(0.3)
    cmd = (
        "docker exec -d -e MODO_ATENCION=PS api1 "
        "sh -c 'uvicorn --app-dir /app servicio:app "
        "--host 0.0.0.0 --port 8000 > /tmp/uvicorn_h3.log 2>&1'"
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
    log_api = ejecutar("docker exec api1 cat /tmp/uvicorn_h3.log", True)
    raise RuntimeError(f"api1 no respondio en modo PS.\n{log_api}")


def medir_rtt_ping():
    out = ejecutar(f"docker exec gen ping -c 50 -i 0.02 {IP_API1}")
    for linea in out.splitlines():
        if "rtt min/avg/max/mdev" in linea or "round-trip" in linea:
            parte_vals = linea.split("=")[1].strip().split()[0]
            nums = [float(x) for x in parte_vals.split("/")]
            return {
                "rtt_min_ms": nums[0],
                "rtt_avg_ms": nums[1],
                "rtt_max_ms": nums[2],
                "rtt_mdev_ms": nums[3],
            }
    raise RuntimeError(f"No se pudo leer el resumen de ping:\n{out}")


def calibrar_red_y_base():
    f_cal = DIR_BASE / "calibracion_red.json"
    if f_cal.exists():
        return json.loads(f_cal.read_text())

    print("Midiendo RTT real de red (ping) y costos base PS (s0, c0)...")
    rtt_info = medir_rtt_ping()

    f_sonda = DIR_BASE / "sonda_base.csv"
    cmd_sonda = (
        f"docker exec gen python3 /app/generador.py "
        f"--url {URL_API1} --lazo cerrado --pedidos 250 "
        f"--d-media 0.0 --dist-d det --r-media 100 --dist-r det "
        f"--semilla 2026 --salida /results/{EXPERIMENTO}/sonda_base.csv"
    )
    ejecutar(cmd_sonda)

    lineas = f_sonda.read_text().splitlines()
    enc = lineas[0].split(",")
    idx_lat = enc.index("latencia_total_ms")
    idx_han = enc.index("t_handler_ms")

    lats, hans = [], []
    for ln in lineas[1:]:
        cols = ln.split(",")
        lats.append(float(cols[idx_lat]))
        hans.append(float(cols[idx_han]))

    arr_lat = np.asarray(lats, dtype=float)
    arr_han = np.asarray(hans, dtype=float)
    arr_c0 = arr_lat - arr_han

    cal = {
        "hora": datetime.now().isoformat(),
        "rtt_ping": rtt_info,
        "s0_medido_ms": float(np.mean(arr_han)),
        "c0_medido_ms": float(np.mean(arr_c0)),
        "c0_std_ms": float(np.std(arr_c0, ddof=1)),
        "overhead_http_ms": float(np.mean(arr_c0) - rtt_info["rtt_avg_ms"]),
    }
    f_cal.write_text(json.dumps(cal, indent=4))
    print(
        f"Calibracion lista -> RTT_ping={cal['rtt_ping']['rtt_avg_ms']:.3f} ms"
        f" | s0={cal['s0_medido_ms']:.4f} ms"
        f" | c0={cal['c0_medido_ms']:.4f} ms"
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
    print(f"Iniciando {EXPERIMENTO} (M/G/1-PS)")
    DIR_BASE.mkdir(parents=True, exist_ok=True)
    limpiar_tc()

    conts = ["gen", "r_wan", "r_bn", "api1"]
    cgroups = {c: ruta_cgroup(c) for c in conts}

    e_d_ms = 10.0
    rhos = [0.5, 0.7, 0.9]
    dists = ["det", "exp", "h2"]
    reps = [1, 2, 3]
    duracion_s = 130.0
    calentamiento_s = 10.0
    dur_util_s = duracion_s - calentamiento_s

    corridas = list(itertools.product(rhos, dists, reps))
    sem_global = zlib.crc32(EXPERIMENTO.encode("utf-8"))
    rng = np.random.default_rng(sem_global)
    rng.shuffle(corridas)

    total = len(corridas)

    try:
        iniciar_servicio_ps()
        cal = calibrar_red_y_base()
        s0_ms = cal["s0_medido_ms"]
        e_s_s = (e_d_ms + s0_ms) / 1000.0

        for idx, (rho, dist, rep) in enumerate(corridas, start=1):
            rho_str = str(rho).replace(".", "")
            nombre = f"PS_rho{rho_str}_{dist}_rep{rep}"
            d_cor = DIR_BASE / nombre
            tasa = rho / e_s_s

            if es_valida(d_cor, tasa, dur_util_s):
                print(f"[{idx}/{total}] {nombre}: ok, salteando...")
                continue

            sem = zlib.crc32(f"{EXPERIMENTO}_{nombre}".encode("utf-8"))
            print(
                f"[{idx}/{total}] {nombre} (rho={rho}, dist={dist}, "
                f"lambda={tasa:.2f} req/s, {duracion_s:.0f}s)..."
            )
            d_cor.mkdir(parents=True, exist_ok=True)

            try:
                d_seg = e_d_ms / 1000.0
                cpu0 = {c: leer_cpu(r) for c, r in cgroups.items()}
                t0 = time.perf_counter()

                ruta_out = f"/results/{EXPERIMENTO}/{nombre}/pedidos.csv"
                cmd_gen = (
                    f"docker exec gen python3 /app/generador.py "
                    f"--url {URL_API1} --lazo abierto "
                    f"--duracion {duracion_s:.1f} "
                    f"--calentamiento {calentamiento_s:.1f} "
                    f"--tasa {tasa:.6f} --d-media {d_seg:.6f} "
                    f"--dist-d {dist} --r-media 100 --dist-r det "
                    f"--semilla {sem} --salida {ruta_out}"
                )
                ejecutar(cmd_gen)

                dur_real = time.perf_counter() - t0
                cpu1 = {c: leer_cpu(r) for c, r in cgroups.items()}

                cpu_d = {}
                for c in cgroups:
                    if cpu0[c] and cpu1[c]:
                        u0 = cpu0[c].get("usage_usec", 0)
                        u1 = cpu1[c].get("usage_usec", 0)
                        t0_th = cpu0[c].get("nr_throttled", 0)
                        t1_th = cpu1[c].get("nr_throttled", 0)
                        th_u0 = cpu0[c].get("throttled_usec", 0)
                        th_u1 = cpu1[c].get("throttled_usec", 0)
                        cpu_d[c] = {
                            "usage_usec": u1 - u0,
                            "nr_throttled": t1_th - t0_th,
                            "throttled_usec": th_u1 - th_u0,
                        }

                f_csv = d_cor / "pedidos.csv"
                n_ok = max(0, len(f_csv.read_text().splitlines()) - 1)

                meta = {
                    "experimento": EXPERIMENTO,
                    "corrida": nombre,
                    "semilla": sem,
                    "hora": datetime.now().isoformat(),
                    "calibracion_red": cal,
                    "parametros": {
                        "modo": "PS",
                        "e_d_ms": e_d_ms,
                        "s0_medido_ms": s0_ms,
                        "c0_medido_ms": cal["c0_medido_ms"],
                        "rtt_ping_avg_ms": cal["rtt_ping"]["rtt_avg_ms"],
                        "rho_objetivo": rho,
                        "tasa_objetivo_rps": tasa,
                        "dist_d": dist,
                        "r_bytes": 100,
                        "duracion_s": duracion_s,
                        "calentamiento_s": calentamiento_s,
                    },
                    "repeticion": rep,
                    "pedidos_ok": n_ok,
                    "duracion_real_s": dur_real,
                    "cpu_cgroup_delta": cpu_d,
                }
                (d_cor / "meta.json").write_text(json.dumps(meta, indent=4))

            except Exception as e:
                print(f"[{idx}/{total}] ERROR en {nombre}: {e}")

    finally:
        detener_uvicorn()
        print("\nExperimento H3 finalizado.")


if __name__ == "__main__":
    main()
