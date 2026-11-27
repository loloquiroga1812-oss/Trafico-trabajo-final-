import subprocess
import json
import time
import zlib
import itertools
from datetime import datetime
from pathlib import Path
import numpy as np

EXPERIMENTO = "T4-Perdida"
DIR_BASE = Path("results") / EXPERIMENTO

C_MBPS = 100
RTTS_MS = [20, 100]
LOSSES_PCT = [0.01, 0.1, 0.5, 1.0, 2.0, 5.0]
ALGORITMOS = ["reno", "cubic", "bbr"]
REPETICIONES = [1, 2, 3]

DURACION_S = 20   # Total por corrida
OMIT_S = 2        # Segundos de arranque omitidos por iperf3 (-O)

# IPs de las interfaces de los routers según compose.yaml
IP_RBN_HACIA_RWAN = "198.18.2.254"   # Salida de datos (cuello de botella TBF)
IP_RWAN_HACIA_CLI = "198.18.1.254"   # Salida de datos (RTT/2 + Loss p%)
IP_RWAN_HACIA_RBN = "198.18.2.253"   # Salida de ACKs  (RTT/2 sin pérdida)
IP_SRV = "198.18.3.10"


def ejecutar(comando, ignorar_error=False):
    res = subprocess.run(comando, shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not ignorar_error:
        raise RuntimeError(f"Fallo en comando: {comando}\nStderr: {res.stderr.strip()}")
    return res.stdout


def obtener_interfaz_por_ip(contenedor, ip_objetivo):
    out = ejecutar(f"docker exec {contenedor} ip -o -4 addr show")
    for linea in out.splitlines():
        if ip_objetivo in linea:
            return linea.split()[1]
    raise RuntimeError(f"No se encontró interfaz con IP {ip_objetivo} en {contenedor}")


def asegurar_iperf_servidor():
    # Uso de pgrep -x para evitar falsos positivos (Sección 9 del enunciado)
    res = subprocess.run("docker exec srv pgrep -x iperf3", shell=True, capture_output=True)
    if res.returncode != 0:
        ejecutar("docker exec srv iperf3 -s -D")
        time.sleep(0.5)


def verificar_bbr_disponible():
    out = ejecutar("docker exec srv sysctl -n net.ipv4.tcp_available_congestion_control")
    disponibles = out.strip().split()
    if "bbr" not in disponibles:
        print("ADVERTENCIA: 'bbr' no está cargado en el kernel. Ejecutá en el host: sudo modprobe tcp_bbr")
    return disponibles


def obtener_ruta_cgroup(contenedor):
    cid = ejecutar(f"docker inspect --format '{{{{.Id}}}}' {contenedor}").strip()
    candidatos = [
        Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat"),
        Path(f"/sys/fs/cgroup/docker/{cid}/cpu.stat"),
    ]
    for ruta in candidatos:
        if ruta.exists():
            return ruta
    return None


def leer_cpu_stat(ruta_cgroup):
    if ruta_cgroup is None or not ruta_cgroup.exists():
        return {}
    datos = {}
    for linea in ruta_cgroup.read_text().splitlines():
        partes = linea.split()
        if len(partes) == 2:
            datos[partes[0]] = int(partes[1])
    return datos


def limpiar_tc(if_bn, if_wan_datos, if_wan_acks):
    ejecutar(f"docker exec r_bn tc qdisc del dev {if_bn} root", ignorar_error=True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_wan_datos} root", ignorar_error=True)
    ejecutar(f"docker exec r_wan tc qdisc del dev {if_wan_acks} root", ignorar_error=True)


def configurar_tc_t4(if_bn, if_wan_datos, if_wan_acks, rtt_ms, p_loss):
    limpiar_tc(if_bn, if_wan_datos, if_wan_acks)

    # 1. Buffer = BDP en el cuello de botella (r_bn -> r_wan)
    bdp_bytes = int((C_MBPS * 1_000_000 / 8.0) * (rtt_ms / 1000.0))
    burst_bytes = 16000  # ~10.5 tramas de 1514 B para sostener 100 Mbit/s
    limit_bytes = max(bdp_bytes, burst_bytes + 3028)

    cmd_tbf = (
        f"tc qdisc add dev {if_bn} root tbf "
        f"rate {C_MBPS}mbit burst {burst_bytes} limit {limit_bytes}"
    )
    ejecutar(f"docker exec r_bn {cmd_tbf}")

    # 2. RTT/2 por sentido y pérdida p% SOLO en sentido de los datos (r_wan -> cli)
    rtt_mitad = rtt_ms / 2.0
    cmd_netem_datos = (
        f"tc qdisc add dev {if_wan_datos} root netem "
        f"delay {rtt_mitad}ms loss {p_loss}% limit 100000"
    )
    cmd_netem_acks = (
        f"tc qdisc add dev {if_wan_acks} root netem "
        f"delay {rtt_mitad}ms limit 100000"
    )
    ejecutar(f"docker exec r_wan {cmd_netem_datos}")
    ejecutar(f"docker exec r_wan {cmd_netem_acks}")

    return {"bdp_bytes": bdp_bytes, "burst_bytes": burst_bytes, "limit_bytes": limit_bytes}


def main():
    print(f"--- Iniciando Experimento: {EXPERIMENTO} ---")
    DIR_BASE.mkdir(parents=True, exist_ok=True)

    asegurar_iperf_servidor()
    algos_disp = verificar_bbr_disponible()
    algos_a_correr = [a for a in ALGORITMOS if a in algos_disp]

    if_bn = obtener_interfaz_por_ip("r_bn", IP_RBN_HACIA_RWAN)
    if_wan_datos = obtener_interfaz_por_ip("r_wan", IP_RWAN_HACIA_CLI)
    if_wan_acks = obtener_interfaz_por_ip("r_wan", IP_RWAN_HACIA_RBN)

    print(f"Interfaces detectadas -> r_bn(TBF): {if_bn} | r_wan(Datos+Loss): {if_wan_datos} | r_wan(ACKs): {if_wan_acks}")

    cgroups = {c: obtener_ruta_cgroup(c) for c in ["cli", "r_wan", "r_bn", "srv"]}

    # Orden aleatorio reproducible con semilla fija (Sección 7)
    corridas = list(itertools.product(RTTS_MS, LOSSES_PCT, algos_a_correr, REPETICIONES))
    rng = np.random.default_rng(zlib.crc32(EXPERIMENTO.encode("utf-8")))
    rng.shuffle(corridas)

    total = len(corridas)
    for idx, (rtt_ms, p_loss, algo, rep) in enumerate(corridas, start=1):
        nombre = f"RTT{rtt_ms}_P{p_loss}_{algo}_rep{rep}"
        dir_corrida = DIR_BASE / nombre
        archivo_meta = dir_corrida / "meta.json"
        archivo_iperf = dir_corrida / "iperf.json"

        if archivo_meta.exists() and archivo_iperf.exists():
            print(f"[{idx}/{total}] {nombre}: ya completado, salteando...")
            continue

        semilla_corrida = zlib.crc32(nombre.encode("utf-8"))
        print(f"[{idx}/{total}] {nombre} (semilla={semilla_corrida})...")
        dir_corrida.mkdir(parents=True, exist_ok=True)

        try:
            tc_info = configurar_tc_t4(if_bn, if_wan_datos, if_wan_acks, rtt_ms, p_loss)
            time.sleep(0.3)

            cpu_antes = {c: leer_cpu_stat(ruta) for c, ruta in cgroups.items()}

            cmd_iperf = (
                f"docker exec cli iperf3 -c {IP_SRV} -R "
                f"-C {algo} -t {DURACION_S} -O {OMIT_S} -i 1 -J"
            )
            salida_json = ejecutar(cmd_iperf)

            cpu_despues = {c: leer_cpu_stat(ruta) for c, ruta in cgroups.items()}

            # Validar JSON antes de guardar
            datos_iperf = json.loads(salida_json)
            if "error" in datos_iperf:
                raise RuntimeError(f"iperf3 reportó error: {datos_iperf['error']}")

            with open(archivo_iperf, "w") as f:
                json.dump(datos_iperf, f, indent=2)

            tc_estado_bn = ejecutar(f"docker exec r_bn tc -s qdisc show dev {if_bn}")
            tc_estado_wan = ejecutar(f"docker exec r_wan tc -s qdisc show dev {if_wan_datos}")

            cpu_delta = {}
            for c in cgroups:
                if cpu_antes[c] and cpu_despues[c]:
                    cpu_delta[c] = {
                        "usage_usec": cpu_despues[c].get("usage_usec", 0) - cpu_antes[c].get("usage_usec", 0),
                        "nr_throttled": cpu_despues[c].get("nr_throttled", 0) - cpu_antes[c].get("nr_throttled", 0),
                    }

            meta = {
                "experimento": EXPERIMENTO,
                "corrida": nombre,
                "semilla": semilla_corrida,
                "hora": datetime.now().isoformat(),
                "parametros": {
                    "capacidad_mbps": C_MBPS,
                    "rtt_ms": rtt_ms,
                    "loss_pct": p_loss,
                    "algoritmo": algo,
                    "repeticion": rep,
                    "duracion_s": DURACION_S,
                    "omit_s": OMIT_S,
                    **tc_info,
                },
                "cpu_cgroup_delta": cpu_delta,
                "tc_estado": {
                    "r_bn": tc_estado_bn.strip(),
                    "r_wan_datos": tc_estado_wan.strip(),
                },
            }
            with open(archivo_meta, "w") as f:
                json.dump(meta, f, indent=4)

        except Exception as e:
            print(f"[{idx}/{total}] ERROR en {nombre}: {e}")

    limpiar_tc(if_bn, if_wan_datos, if_wan_acks)
    print(f"--- Experimento {EXPERIMENTO} finalizado ---")


if __name__ == "__main__":
    main()
