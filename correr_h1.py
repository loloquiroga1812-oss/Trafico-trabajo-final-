import subprocess
import json
import time
import zlib
import itertools
from datetime import datetime
from pathlib import Path
import numpy as np

EXPERIMENTO = "H1"
DIR_BASE = Path("results") / EXPERIMENTO
URL_API1 = "http://198.18.3.11:8000/t"


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


def aplicar_cupo_cpu(cupo_pct):
    if cupo_pct >= 100:
        quota = -1
    else:
        quota = int(10000 * (cupo_pct / 100.0))
    cmd = f"docker update --cpu-period=10000 --cpu-quota={quota} api1"
    ejecutar(cmd)


def iniciar_servicio(modo):
    ejecutar("docker exec api1 pkill -f '[u]vicorn servicio:app'", True)
    time.sleep(0.3)
    cmd = (
        f"docker exec -d -e MODO_ATENCION={modo} api1 "
        f"uvicorn servicio:app --host 0.0.0.0 --port 8000"
    )
    ejecutar(cmd)

    py_check = (
        "import urllib.request; "
        "r=urllib.request.urlopen('http://198.18.3.11:8000/t?d=0&r=1'); "
        "print(r.status)"
    )
    for _ in range(30):
        out = ejecutar(f'docker exec gen python3 -c "{py_check}"', True)
        if "200" in out:
            return
        time.sleep(0.2)
    raise RuntimeError(f"api1 no respondio en modo {modo}")


def es_valida(d_cor):
    f_meta = d_cor / "meta.json"
    f_csv = d_cor / "pedidos.csv"
    if not f_meta.exists() or not f_csv.exists():
        return False
    try:
        meta = json.loads(f_meta.read_text())
        return meta.get("pedidos_ok", 0) >= 300
    except Exception:
        return False


def correr_serie(serie, lista_corridas, cgroups):
    dir_serie = DIR_BASE / serie
    dir_serie.mkdir(parents=True, exist_ok=True)

    sem_serie = zlib.crc32(f"{EXPERIMENTO}_{serie}".encode("utf-8"))
    rng = np.random.default_rng(sem_serie)
    rng.shuffle(lista_corridas)

    modo_actual = None
    cupo_actual = None
    total = len(lista_corridas)

    for idx, (modo, cupo, d_ms, rep) in enumerate(lista_corridas, start=1):
        nombre = f"{modo}_C{cupo}_D{d_ms}_rep{rep}"
        d_cor = dir_serie / nombre

        if es_valida(d_cor):
            print(f"[{serie} {idx}/{total}] {nombre}: ok, salteando...")
            continue

        if modo != modo_actual:
            iniciar_servicio(modo)
            modo_actual = modo

        if cupo != cupo_actual:
            aplicar_cupo_cpu(cupo)
            cupo_actual = cupo

        sem = zlib.crc32(f"{serie}_{nombre}".encode("utf-8"))
        print(f"[{serie} {idx}/{total}] {nombre} (sem={sem}) -> 300 pedidos...")
        d_cor.mkdir(parents=True, exist_ok=True)

        try:
            d_seg = d_ms / 1000.0
            cpu0 = {c: leer_cpu(r) for c, r in cgroups.items()}
            t0 = time.perf_counter()

            ruta_out = f"/results/{EXPERIMENTO}/{serie}/{nombre}/pedidos.csv"
            cmd_gen = (
                f"docker exec gen python3 /app/generador.py "
                f"--url {URL_API1} --lazo cerrado --pedidos 300 "
                f"--d-media {d_seg:.6f} --dist-d det "
                f"--r-media 100 --dist-r det --semilla {sem} "
                f"--salida {ruta_out}"
            )
            ejecutar(cmd_gen)

            duracion_s = time.perf_counter() - t0
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
                "serie": serie,
                "corrida": nombre,
                "semilla": sem,
                "hora": datetime.now().isoformat(),
                "parametros": {
                    "modo": modo,
                    "cupo_cpu_pct": cupo,
                    "cpu_period_us": 10000,
                    "d_ms": d_ms,
                    "r_bytes": 100,
                    "pedidos": 300,
                },
                "repeticion": rep,
                "pedidos_ok": n_ok,
                "duracion_s": duracion_s,
                "cpu_cgroup_delta": cpu_d,
            }
            (d_cor / "meta.json").write_text(json.dumps(meta, indent=4))

        except Exception as e:
            print(f"[{serie} {idx}/{total}] ERROR en {nombre}: {e}")


def main():
    print(f"Iniciando {EXPERIMENTO} (orden estricto de consigna)")
    DIR_BASE.mkdir(parents=True, exist_ok=True)
    limpiar_tc()

    conts = ["gen", "r_wan", "r_bn", "api1"]
    cgroups = {c: ruta_cgroup(c) for c in conts}

    demandas_ms = [0, 1, 2, 5, 10, 20]
    modos = ["FIFO", "PS"]
    cupos_pct = [100, 50, 25]
    reps = [1, 2, 3]

    try:
        # ETAPA 1: Pedidos de a uno con D en {0,1,2,5,10,20} ms en ambos modos
        print("\n=== ETAPA 1: Caracterizacion base (FIFO y PS, 100% CPU) ===")
        corridas_base = [
            (modo, 100, d_ms, rep)
            for modo, d_ms, rep in itertools.product(modos, demandas_ms, reps)
        ]
        correr_serie("base", corridas_base, cgroups)

        # ETAPA 2: Cupo de CPU de 100, 50 y 25% con cada D
        print("\n=== ETAPA 2: Barrido de cupo de CPU (100%, 50%, 25%) ===")
        corridas_cupo = [
            ("FIFO", cupo, d_ms, rep)
            for cupo, d_ms, rep in itertools.product(
                cupos_pct, demandas_ms, reps
            )
        ]
        correr_serie("cupo", corridas_cupo, cgroups)

    finally:
        aplicar_cupo_cpu(100)
        ejecutar("docker exec api1 pkill -f '[u]vicorn servicio:app'", True)
        print("\nExperimento H1 finalizado (cupo api1 restaurado al 100%).")


if __name__ == "__main__":
    main()
