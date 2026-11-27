import os
import sys
import time
from contextlib import asynccontextmanager
import anyio
from fastapi import FastAPI, Response

MODO = os.getenv("MODO_ATENCION", "FIFO").upper()
MAX_HILOS = int(os.getenv("MAX_HILOS", "200"))


def quemar_cpu(segundos: float) -> float:
    if segundos <= 0.0:
        return 0.0
    inicio = time.thread_time()
    val = 1.0001
    while (time.thread_time() - inicio) < segundos:
        for _ in range(50):
            val = (val * 1.000001 + 0.000001) % 1000.0
    return time.thread_time() - inicio


def ejecutar_pedido(d: float, r: int) -> Response:
    t_enter = time.perf_counter()
    cpu_usada = quemar_cpu(d)
    cuerpo = b"x" * max(0, r)
    t_exit = time.perf_counter()

    cabeceras = {
        "X-CPU-Sec": f"{cpu_usada:.9f}",
        "X-T-Enter": f"{t_enter:.9f}",
        "X-T-Exit": f"{t_exit:.9f}",
        "X-Modo": MODO,
    }
    return Response(
        content=cuerpo,
        media_type="application/octet-stream",
        headers=cabeceras,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if MODO == "PS":
        sys.setswitchinterval(0.001)
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = MAX_HILOS
    yield


app = FastAPI(lifespan=lifespan)

if MODO == "PS":
    @app.get("/t")
    def endpoint_ps(d: float = 0.0, r: int = 100):
        return ejecutar_pedido(d, r)
else:
    @app.get("/t")
    async def endpoint_fifo(d: float = 0.0, r: int = 100):
        return ejecutar_pedido(d, r)
