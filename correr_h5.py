import subprocess
import json
import time
import zlib
import itertools
from datetime import datetime
from pathlib import Path
import numpy as np

EXPERIMENTO = "H5"
DIR_BASE = Path("results") / EXPERIMENTO
POLITICAS = ["random", "roundrobin", "random2", "leastconn", "central"]


def ejecutar(cmd, ignorar_error=False):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not ignorar_error:
        raise RuntimeError(f"Fallo: {cmd}\n{res.stderr.strip()}")
    return res.stdout


def obtener_ips_contenedor(cont):
    fmt = "'{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'"
    out = ejecutar(f"docker inspect --format {fmt} {cont}").strip()
    return [ip for ip in out.split() if ip]


def detectar_red_h5():
    ips_lb = obtener_ips_contenedor("lb")
    if not ips_lb:
        raise RuntimeError("No se encontro IP para el contenedor lb.")

    # Buscar que IP de lb es alcanzable desde gen
    ip_lb_front = None
    for ip in ips_lb:
        out = ejecutar(f"docker exec gen ping -c 1 -W 1 {ip}", True)
        if "1 received" in out or "1 packets received" in out:
            ip_lb_front = ip
            break
    if ip_lb_front is None:
        ip_lb_front = ips_lb[0]

    ips_apis = {}
    for i in [1, 2, 3, 4]:
        nombre = f"api{i}"
        cands = obtener_ips_contenedor(nombre)
        ip_3 = next((x for x in cands if x.startswith("198.18.3.")), cands[0])
        ips_apis[nombre] = ip_3

    return ip_lb_front, ips_apis


def limpiar_tc_routers():
    for cont in ["r_bn", "r_wan"]:
        out = ejecutar(f"docker exec {cont} ip -o -4 addr show", True)
        for linea in out.splitlines():
            partes = linea.split()
            if len(partes) >= 2:
                iface = partes[1]
                ejecutar(f"docker exec {cont} tc qdisc del dev {iface} root", True)


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


def detener_procesos_api(cont):
    kill_py = (
        "import os, signal; "
        "[os.kill(int(p), signal.SIGKILL) for p in os.listdir('/proc') "
        "if p.isdigit() and int(p) != os.getpid() "
        "and b'uvicorn' in open(f'/proc/{p}/cmdline', 'rb').read()]"
    )
    ejecutar(f'docker exec {cont} python3 -c "{kill_py}"', True)


def iniciar_servidores_fifo(ips_apis):
    for cont, ip in ips_apis.items():
        ejecutar(f"docker update --cpu-period=10000 --cpu-quota=-1 {cont}", True)
        detener_procesos_api(cont)
        time.sleep(0.15)
        cmd = (
            f"docker exec -d -e MODO_ATENCION=FIFO {cont} "
            f"sh -c 'uvicorn --app-dir /app servicio:app "
            f"--host 0.0.0.0 --port 8000 > /tmp/uvicorn_h5.log 2>&1'"
        )
        ejecutar(cmd)

    for cont, ip in ips_apis.items():
        py_check = (
            f"import urllib.request; "
            f"r=urllib.request.urlopen('http://{ip}:8000/t?d=0&r=1', timeout=1); "
            f"print(r.status)"
        )
        ok = False
        for _ in range(30):
            out = ejecutar(f'docker exec gen python3 -c "{py_check}"', True)
            if "200" in out:
                ok = True
                break
            time.sleep(0.2)
        if not ok:
            log_api = ejecutar(f"docker exec {cont} cat /tmp/uvicorn_h5.log", True)
            raise RuntimeError(f"{cont} ({ip}) no respondio en modo FIFO.\n{log_api}")


def detener_haproxy_y_logger():
    kill_lb = (
        "import os, signal; "
        "[os.kill(int(p), signal.SIGKILL) for p in os.listdir('/proc') "
        "if p.isdigit() and int(p) != os.getpid() "
        "and (b'haproxy' in open(f'/proc/{p}/cmdline', 'rb').read() "
        "or b'udp_logger_h5' in open(f'/proc/{p}/cmdline', 'rb').read())]"
    )
    ejecutar(f'docker exec lb python3 -c "{kill_lb}"', True)


def iniciar_logger_lb():
    # Receptor UDP dentro de lb para grabar servidor asignado por HAProxy
    py_srv = (
        "import socket\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "s.bind(('127.0.0.1', 5514))\n"
        "f = open('/tmp/haproxy_trace.log', 'w', buffering=1)\n"
        "while True:\n"
        "    data, _ = s.recvfrom(4096)\n"
        "    txt = data.decode('utf-8', 'ignore')\n"
        "    if 'H5TRACE:' in txt:\n"
        "        f.write(txt.split('H5TRACE:', 1)[1].strip() + '\\n')\n"
    )
    Path("/tmp/udp_logger_h5.py").write_text(py_srv)
    ejecutar("docker cp /tmp/udp_logger_h5.py lb:/tmp/udp_logger_h5.py")
    ejecutar("docker exec -d lb python3 /tmp/udp_logger_h5.py")


def configurar_haproxy(politica, k_servidores, ips_apis, ip_lb):
    detener_haproxy_y_logger()
    time.sleep(0.2)
    iniciar_logger_lb()

    if politica == "random":
        # OJO: "balance random" a secas es random(2) en HAProxy (draws=2 por defecto).
        # El random teorico (un sorteo, K colas M/G/1 independientes) es random(1).
        bal_line = "    balance random(1)"
        srv_extra = ""
    elif politica == "roundrobin":
        bal_line = "    balance roundrobin"
        srv_extra = ""
    elif politica == "random2":
        bal_line = "    balance random(2)"
        srv_extra = ""
    elif politica == "leastconn":
        bal_line = "    balance leastconn"
        srv_extra = ""
    elif politica == "central":
        bal_line = "    balance leastconn"
        srv_extra = " maxconn 1"
    else:
        raise ValueError(f"Politica desconocida: {politica}")

    lineas_srv = []
    for i in range(1, k_servidores + 1):
        nombre = f"api{i}"
        ip = ips_apis[nombre]
        lineas_srv.append(f"    server {nombre} {ip}:8000{srv_extra}")

    cfg = "\n".join([
        "global",
        "    maxconn 8192",
        "    log 127.0.0.1:5514 local0",
        "",
        "defaults",
        "    mode http",
        "    log global",
        "    option http-keep-alive",
        "    option dontlognull",
        "    timeout connect 5s",
        "    timeout client 60s",
        "    timeout server 60s",
        "    timeout queue 60s",
        "",
        "frontend fe_http",
        "    bind *:8000",
        "    log-format H5TRACE:%s,%Tq,%Tw,%Tc,%Tr,%Tt",
        "    stats enable",
        "    stats uri /haproxy_stats",
        "    default_backend be_apis",
        "",
        "backend be_apis",
        bal_line,
        *lineas_srv,
        "",
    ])

    Path("/tmp/haproxy_h5.cfg").write_text(cfg)
    ejecutar("docker cp /tmp/haproxy_h5.cfg lb:/tmp/haproxy_h5.cfg")
    ejecutar("docker exec -d lb haproxy -f /tmp/haproxy_h5.cfg")

    py_check = (
        f"import urllib.request; "
        f"r=urllib.request.urlopen('http://{ip_lb}:8000/t?d=0&r=1', timeout=1); "
        f"print(r.status)"
    )
    for _ in range(30):
        out = ejecutar(f'docker exec gen python3 -c "{py_check}"', True)
        if "200" in out:
            return
        time.sleep(0.2)
    raise RuntimeError(f"HAProxy no respondio en {ip_lb}:8000 para {politica}")


def leer_stats_haproxy(ip_lb):
    py_st = (
        f"import urllib.request; "
        f"txt=urllib.request.urlopen('http://{ip_lb}:8000/haproxy_stats;csv', timeout=2).read().decode(); "
        f"print(txt)"
    )
    out = ejecutar(f'docker exec gen python3 -c "{py_st}"', True)
    conteos = {}
    lineas = [ln for ln in out.splitlines() if ln.strip()]
    if not lineas:
        return conteos
    enc = lineas[0].lstrip("# ").split(",")
    if "pxname" not in enc or "svname" not in enc or "stot" not in enc:
        return conteos
    i_px = enc.index("pxname")
    i_sv = enc.index("svname")
    i_st = enc.index("stot")
    for ln in lineas[1:]:
        cols = ln.split(",")
        if len(cols) > max(i_px, i_sv, i_st):
            if cols[i_px] == "be_apis" and cols[i_sv].startswith("api"):
                conteos[cols[i_sv]] = int(cols[i_st] or 0)
    return conteos


def medir_sonda(url_obj, nombre_csv, n_ped=250):
    f_sonda = DIR_BASE / nombre_csv
    cmd = (
        f"docker exec gen python3 /app/generador.py "
        f"--url {url_obj} --lazo cerrado --pedidos {n_ped} "
        f"--d-media 0.0 --dist-d det --r-media 100 --dist-r det "
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
    return float(np.mean(lats)), float(np.mean(hans)), len(lats)


def calibrar_h5(ip_lb, ips_apis):
    f_cal = DIR_BASE / "calibracion_lb.json"
    if f_cal.exists():
        return json.loads(f_cal.read_text())

    print("Calibrando tiempos en vacio directo vs. a traves de HAProxy...")

    # s0 por dos caminos:
    #  - handler: t_handler_ms (solo lo que corre dentro del handler)
    #  - cgroup : CPU real de api1 por pedido (incluye el parseo HTTP de uvicorn,
    #             que tambien bloquea el event loop y por lo tanto es servicio)
    cg_api1 = ruta_cgroup("api1")
    u0 = leer_cpu(cg_api1).get("usage_usec", 0)
    t_dir_ms, s0_handler_ms, n_dir = medir_sonda(
        f"http://{ips_apis['api1']}:8000/t", "sonda_directa.csv", n_ped=250
    )
    u1 = leer_cpu(cg_api1).get("usage_usec", 0)
    s0_cg_ms = (u1 - u0) / 1000.0 / max(1, n_dir)
    s0_ms = max(s0_handler_ms, s0_cg_ms)

    configurar_haproxy("roundrobin", 2, ips_apis, ip_lb)
    t_lb_ms, _, _ = medir_sonda(
        f"http://{ip_lb}:8000/t", "sonda_haproxy.csv", n_ped=250
    )

    c0_directo_ms = max(0.05, t_dir_ms - s0_ms)
    c0_lb_ms = max(0.10, t_lb_ms - s0_ms)
    # Salto ida y vuelta extra por pasar a traves de HAProxy (gap de despacho maxconn 1)
    gap_lb_api_ms = max(0.05, t_lb_ms - t_dir_ms)

    cal = {
        "hora": datetime.now().isoformat(),
        "ip_lb": ip_lb,
        "ips_apis": ips_apis,
        "s0_ms": s0_ms,
        "s0_handler_ms": s0_handler_ms,
        "s0_cgroup_ms": s0_cg_ms,
        "t_vacio_directo_ms": t_dir_ms,
        "t_vacio_lb_ms": t_lb_ms,
        "c0_directo_ms": c0_directo_ms,
        "c0_lb_ms": c0_lb_ms,
        "gap_lb_api_ms": gap_lb_api_ms,
    }
    f_cal.write_text(json.dumps(cal, indent=4))
    print(
        f"Calibracion lista -> s0={s0_ms:.4f} ms "
        f"(handler={s0_handler_ms:.4f}, cgroup={s0_cg_ms:.4f}) | "
        f"c0_lb={c0_lb_ms:.4f} ms | gap_lb_api={gap_lb_api_ms:.4f} ms"
    )
    return cal


def es_valida(d_cor, tasa_esp, dur_util):
    f_meta = d_cor / "meta.json"
    f_csv = d_cor / "pedidos.csv"
    if not f_meta.exists() or not f_csv.exists():
        return False
    try:
        meta = json.loads(f_meta.read_text())
        min_ped = int(tasa_esp * dur_util * 0.70)
        return meta.get("pedidos_ok", 0) >= max(50, min_ped)
    except Exception:
        return False


def main():
    print(f"Iniciando {EXPERIMENTO} (Balanceo con HAProxy)")
    DIR_BASE.mkdir(parents=True, exist_ok=True)

    limpiar_tc_routers()
    ip_lb, ips_apis = detectar_red_h5()
    url_lb = f"http://{ip_lb}:8000/t"

    conts = ["gen", "r_wan", "r_bn", "lb", "api1", "api2", "api3", "api4"]
    cgroups = {c: ruta_cgroup(c) for c in conts}

    e_d_ms = 10.0
    ks = [2, 4]
    rhos = [0.6, 0.9]
    reps = [1, 2, 3]
    duracion_s = 130.0          # antes 70 s; el enunciado pide >= 120 s
    calentamiento_s = 10.0
    dur_util_s = duracion_s - calentamiento_s

    corridas = list(itertools.product(ks, rhos, POLITICAS, reps))
    sem_global = zlib.crc32(EXPERIMENTO.encode("utf-8"))
    rng = np.random.default_rng(sem_global)
    rng.shuffle(corridas)
    total = len(corridas)

    try:
        iniciar_servidores_fifo(ips_apis)
        cal = calibrar_h5(ip_lb, ips_apis)
        s0_ms = cal["s0_ms"]
        e_s_s = (e_d_ms + s0_ms) / 1000.0

        for idx, (k_srv, rho_obj, pol, rep) in enumerate(corridas, start=1):
            r_str = str(rho_obj).replace(".", "")
            nombre = f"K{k_srv}_rho{r_str}_{pol}_rep{rep}"
            d_cor = DIR_BASE / nombre

            tasa = (k_srv * rho_obj) / e_s_s

            if es_valida(d_cor, tasa, dur_util_s):
                print(f"[{idx}/{total}] {nombre}: ok, salteando...")
                continue

            sem = zlib.crc32(f"{EXPERIMENTO}_{nombre}".encode("utf-8"))
            print(
                f"[{idx}/{total}] {nombre} (K={k_srv}, rho={rho_obj}, "
                f"pol={pol}, lambda={tasa:.2f} req/s)..."
            )
            d_cor.mkdir(parents=True, exist_ok=True)

            try:
                configurar_haproxy(pol, k_srv, ips_apis, ip_lb)
                st0 = leer_stats_haproxy(ip_lb)
                cpu0 = {c: leer_cpu(r) for c, r in cgroups.items()}
                t0 = time.perf_counter()

                d_seg = e_d_ms / 1000.0
                ruta_out = f"/results/{EXPERIMENTO}/{nombre}/pedidos.csv"
                cmd_gen = (
                    f"docker exec gen sh -c 'ulimit -n 65535 && python3 /app/generador.py "
                    f"--url {url_lb} --lazo abierto "
                    f"--duracion {duracion_s:.1f} "
                    f"--calentamiento {calentamiento_s:.1f} "
                    f"--tasa {tasa:.6f} --d-media {d_seg:.6f} "
                    f"--dist-d exp --r-media 100 --dist-r det "
                    f"--semilla {sem} --salida {ruta_out}'"
                )
                ejecutar(cmd_gen)

                dur_real = time.perf_counter() - t0
                st1 = leer_stats_haproxy(ip_lb)
                cpu1 = {c: leer_cpu(r) for c, r in cgroups.items()}

                # Copiar traza exacta de asignacion de HAProxy
                ejecutar(
                    f"docker cp lb:/tmp/haproxy_trace.log {d_cor / 'haproxy_trace.csv'}",
                    True,
                )

                reparto_stats = {}
                for i in range(1, k_srv + 1):
                    sv = f"api{i}"
                    reparto_stats[sv] = max(0, st1.get(sv, 0) - st0.get(sv, 0))

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

                f_csv = d_cor / "pedidos.csv"
                n_ok = max(0, len(f_csv.read_text().splitlines()) - 1)

                meta = {
                    "experimento": EXPERIMENTO,
                    "corrida": nombre,
                    "semilla": sem,
                    "hora": datetime.now().isoformat(),
                    "calibracion_lb": cal,
                    "parametros": {
                        "modo": "FIFO",
                        "k_servidores": k_srv,
                        "politica": pol,
                        "e_d_ms": e_d_ms,
                        "rho_objetivo": rho_obj,
                        "tasa_objetivo_rps": tasa,
                        "duracion_s": duracion_s,
                        "calentamiento_s": calentamiento_s,
                    },
                    "repeticion": rep,
                    "pedidos_ok": n_ok,
                    "duracion_real_s": dur_real,
                    "reparto_haproxy_stats": reparto_stats,
                    "cpu_cgroup_delta": cpu_d,
                }
                (d_cor / "meta.json").write_text(json.dumps(meta, indent=4))

            except Exception as e:
                print(f"[{idx}/{total}] ERROR en {nombre}: {e}")

    finally:
        detener_haproxy_y_logger()
        for cont in ips_apis:
            detener_procesos_api(cont)
        print("\nExperimento H5 finalizado.")


if __name__ == "__main__":
    main()
