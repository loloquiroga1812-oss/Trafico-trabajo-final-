import argparse
import asyncio
import csv
import math
import time
from pathlib import Path
import aiohttp
import numpy as np


def generar_demandas(rng, n, media_s, dist):
    if dist == "det" or media_s <= 0.0:
        return np.full(n, media_s, dtype=float)
    if dist == "exp":
        return rng.exponential(media_s, size=n)
    if dist == "h2":
        c2 = 5.0
        p1 = 0.5 * (1.0 + math.sqrt((c2 - 1.0) / (c2 + 1.0)))
        m1 = media_s / (2.0 * p1)
        m2 = media_s / (2.0 * (1.0 - p1))
        eleccion = rng.random(size=n) < p1
        s1 = rng.exponential(m1, size=n)
        s2 = rng.exponential(m2, size=n)
        return np.where(eleccion, s1, s2)
    raise ValueError(f"Distribucion desconocida: {dist}")


def generar_tamanos(rng, n, media_r, dist_r):
    if dist_r == "exp" and media_r > 0:
        vals = rng.exponential(float(media_r), size=n)
        return np.maximum(1, np.round(vals)).astype(int)
    return np.full(n, int(media_r), dtype=int)


async def enviar_uno(session, url, req_id, t_prog, d_s, r_b, registros):
    params = {"d": f"{d_s:.9f}", "r": str(int(r_b))}
    t_envio = time.perf_counter()
    async with session.get(url, params=params) as resp:
        await resp.read()
        t_resp = time.perf_counter()
        cpu_usada = float(resp.headers.get("X-CPU-Sec", "0"))
        t_enter = float(resp.headers.get("X-T-Enter", "0"))
        t_exit = float(resp.headers.get("X-T-Exit", "0"))
        status = resp.status

    registros.append({
        "id": req_id,
        "t_prog_s": t_prog,
        "t_envio_s": t_envio,
        "t_resp_s": t_resp,
        "atraso_envio_ms": (t_envio - t_prog) * 1000.0,
        "latencia_total_ms": (t_resp - t_envio) * 1000.0,
        "t_enter_s": t_enter,
        "t_exit_s": t_exit,
        "t_handler_ms": (t_exit - t_enter) * 1000.0,
        "cpu_pedida_ms": d_s * 1000.0,
        "cpu_usada_ms": cpu_usada * 1000.0,
        "bytes_r": int(r_b),
        "status": status,
    })


async def correr(args):
    rng = np.random.default_rng(args.semilla)
    connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:
        for _ in range(10):
            async with session.get(
                args.url, params={"d": "0.0001", "r": "64"}
            ) as resp:
                await resp.read()

        registros = []

        if args.lazo == "cerrado":
            n = args.pedidos
            demandas = generar_demandas(rng, n, args.d_media, args.dist_d)
            tamanos = generar_tamanos(rng, n, args.r_media, args.dist_r)
            for i in range(n):
                ahora = time.perf_counter()
                await enviar_uno(
                    session, args.url, i, ahora,
                    float(demandas[i]), int(tamanos[i]), registros
                )
        else:
            n_est = int(args.tasa * args.duracion * 1.4) + 200
            inter = rng.exponential(1.0 / args.tasa, size=n_est)
            offsets = np.cumsum(inter)
            offsets = offsets[offsets <= args.duracion]
            n = len(offsets)
            demandas = generar_demandas(rng, n, args.d_media, args.dist_d)
            tamanos = generar_tamanos(rng, n, args.r_media, args.dist_r)

            t0 = time.perf_counter()
            tareas = []
            for i in range(n):
                objetivo = t0 + float(offsets[i])
                restante = objetivo - time.perf_counter()
                if restante > 0.0015:
                    await asyncio.sleep(restante - 0.001)
                while time.perf_counter() < objetivo:
                    await asyncio.sleep(0)

                t = asyncio.create_task(
                    enviar_uno(
                        session, args.url, i, objetivo,
                        float(demandas[i]), int(tamanos[i]), registros
                    )
                )
                tareas.append(t)

            await asyncio.gather(*tareas)
            t_corte = t0 + args.calentamiento
            registros = [r for r in registros if r["t_envio_s"] >= t_corte]

    registros.sort(key=lambda x: x["id"])
    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    if registros:
        with salida.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(registros[0].keys()))
            writer.writeheader()
            writer.writerows(registros)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://198.18.3.11:8000/t")
    p.add_argument("--lazo", choices=["cerrado", "abierto"], required=True)
    p.add_argument("--pedidos", type=int, default=300)
    p.add_argument("--duracion", type=float, default=130.0)
    p.add_argument("--calentamiento", type=float, default=10.0)
    p.add_argument("--tasa", type=float, default=50.0)
    p.add_argument("--d-media", type=float, default=0.010)
    p.add_argument("--dist-d", choices=["det", "exp", "h2"], default="det")
    p.add_argument("--r-media", type=int, default=100)
    p.add_argument("--dist-r", choices=["det", "exp"], default="det")
    p.add_argument("--semilla", type=int, default=12345)
    p.add_argument("--salida", required=True)
    args = p.parse_args()
    asyncio.run(correr(args))


if __name__ == "__main__":
    main()
