#!/usr/bin/env python3
"""Sonda UDP de eco (corre en cli). Mide RTT por paquete y pérdida.

Manda N datagramas numerados a intervalos fijos hacia eco.py. Escribe por stdout un CSV:
    seq,t_send_ns,t_recv_ns      (t_recv_ns = -1 si el eco no volvió)
Los tiempos son de time.perf_counter_ns() del mismo proceso (relativos al primer envío).
La pérdida configurada en netem solo actúa en el sentido srv -> cli, o sea sobre el eco:
un eco que no vuelve = un paquete de datos perdido.
"""
import argparse, socket, struct, sys, threading, time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--puerto", type=int, default=9000)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--intervalo-ms", type=float, required=True)
    ap.add_argument("--tam", type=int, default=64)
    ap.add_argument("--espera-final-s", type=float, default=2.0)
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    s.bind(("0.0.0.0", 0))
    s.settimeout(0.1)

    t_send = [-1] * a.n
    t_recv = [-1] * a.n
    fin = threading.Event()

    def receptor():
        while True:
            try:
                datos, _ = s.recvfrom(2048)
            except socket.timeout:
                if fin.is_set():
                    return
                continue
            t = time.perf_counter_ns()
            if len(datos) >= 4:
                seq = struct.unpack("!I", datos[:4])[0]
                if seq < a.n and t_recv[seq] < 0:
                    t_recv[seq] = t

    th = threading.Thread(target=receptor, daemon=True)
    th.start()

    relleno = bytes(max(a.tam - 4, 0))
    paso = a.intervalo_ms / 1000.0
    t0 = time.perf_counter()
    for i in range(a.n):
        objetivo = t0 + i * paso
        while True:                      # espera híbrida: sleep + sleep(0)
            resto = objetivo - time.perf_counter()
            if resto <= 0:
                break
            if resto > 0.0005:
                time.sleep(resto - 0.0003)
            else:
                time.sleep(0)
        t_send[i] = time.perf_counter_ns()
        s.sendto(struct.pack("!I", i) + relleno, (a.dest, a.puerto))
    atraso_max_ms = max(
        ((t_send[i] - t_send[0]) / 1e6 - i * a.intervalo_ms) for i in range(a.n)
    )

    time.sleep(a.espera_final_s)
    fin.set()
    th.join(timeout=2)

    base = t_send[0]
    out = ["seq,t_send_ns,t_recv_ns"]
    for i in range(a.n):
        out.append(f"{i},{t_send[i]-base},{(t_recv[i]-base) if t_recv[i] >= 0 else -1}")
    sys.stdout.write("\n".join(out) + "\n")
    perdidos = sum(1 for x in t_recv if x < 0)
    print(f"n={a.n} perdidos={perdidos} atraso_envio_max_ms={atraso_max_ms:.3f}", file=sys.stderr)


if __name__ == "__main__":
    main()
