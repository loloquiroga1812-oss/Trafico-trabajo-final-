import json
import math
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

try:
    from scipy import stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

DIR_BASE = Path("results/T4-Perdida")

# Constantes físicas del enlace (Sección 4 y 6.1)
C_MBPS = 100.0
MSS_BYTES = 1448.0
FRAME_BYTES = 1514.0
ETA = MSS_BYTES / FRAME_BYTES          # ~0.9564
ETA_C_MBPS = ETA * C_MBPS              # ~95.64 Mbit/s


def modelo_mathis_mbps(rtt_ms, p_pct):
    rtt_s = rtt_ms / 1000.0
    p = p_pct / 100.0
    bps = (MSS_BYTES * 8.0 / rtt_s) * math.sqrt(3.0 / (2.0 * p))
    return min(ETA_C_MBPS, bps / 1e6)


def modelo_pftk_mbps(rtt_ms, p_pct, b=2):
    rtt_s = rtt_ms / 1000.0
    p = p_pct / 100.0
    t0 = max(0.2, 4.0 * rtt_s)
    termino_ca = rtt_s * math.sqrt(2.0 * b * p / 3.0)
    termino_to = t0 * min(1.0, 3.0 * math.sqrt(3.0 * b * p / 8.0)) * p * (1.0 + 32.0 * (p ** 2))
    pkts_por_seg = 1.0 / (termino_ca + termino_to)
    bps = pkts_por_seg * MSS_BYTES * 8.0
    return min(ETA_C_MBPS, bps / 1e6)


def modelo_cubic_rfc9438_mbps(rtt_ms, p_pct, c_cubic=0.4, beta=0.7):
    rtt_s = rtt_ms / 1000.0
    p = p_pct / 100.0
    factor = (((3.0 + beta) / 4.0) * (c_cubic / (1.0 - beta))) ** 0.25
    w_avg_pkts = factor * ((rtt_s / p) ** 0.75)
    bps_cubic = (w_avg_pkts * MSS_BYTES * 8.0) / rtt_s
    mbps_cubic = bps_cubic / 1e6
    mbps_reno = modelo_mathis_mbps(rtt_ms, p_pct)
    return min(ETA_C_MBPS, max(mbps_cubic, mbps_reno))


def modelo_bbr_mbps(rtt_ms, p_pct):
    p = p_pct / 100.0
    return ETA_C_MBPS * (1.0 - p)


def t_critico_95(df):
    if HAS_SCIPY:
        return float(stats.t.ppf(0.975, df))
    tabla = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 10: 2.228}
    return tabla.get(df, 1.96)


def ic_media_t(valores):
    arr = np.asarray(valores, dtype=float)
    n = len(arr)
    media = float(np.mean(arr))
    if n < 2:
        return media, 0.0, 0.0
    std = float(np.std(arr, ddof=1))
    margen = t_critico_95(n - 1) * (std / math.sqrt(n))
    return media, std, margen


def regresion_loglog(p_arr, x_arr):
    log_p = np.log10(p_arr)
    log_x = np.log10(x_arr)
    n = len(log_p)
    if n < 3:
        return float("nan"), float("nan"), float("nan")

    if HAS_SCIPY:
        res = stats.linregress(log_p, log_x)
        pendiente = float(res.slope)
        r2 = float(res.rvalue ** 2)
        margen_m = t_critico_95(n - 2) * float(res.stderr)
        return pendiente, margen_m, r2
    else:
        pendiente, intercepto = np.polyfit(log_p, log_x, 1)
        y_pred = pendiente * log_p + intercepto
        ss_res = np.sum((log_x - y_pred) ** 2)
        ss_tot = np.sum((log_x - np.mean(log_x)) ** 2)
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        s_err = math.sqrt(ss_res / (n - 2)) / math.sqrt(np.sum((log_p - np.mean(log_p)) ** 2))
        margen_m = t_critico_95(n - 2) * s_err
        return float(pendiente), float(margen_m), float(r2)


def main():
    if not DIR_BASE.exists():
        print(f"No existe {DIR_BASE}. Ejecutá primero correr_T4_perdida.py")
        return

    registros = []
    alertas_cordura = []

    for d in sorted(DIR_BASE.iterdir()):
        if not d.is_dir():
            continue
        f_meta = d / "meta.json"
        f_iperf = d / "iperf.json"
        if not f_meta.exists() or not f_iperf.exists():
            continue

        meta = json.loads(f_meta.read_text())
        iperf = json.loads(f_iperf.read_text())

        params = meta["parametros"]
        rtt_ms = params["rtt_ms"]
        p_loss = params["loss_pct"]
        algo = params["algoritmo"]
        rep = params["repeticion"]

        intervalos = iperf.get("intervals", [])
        intervalos_limpios = intervalos[1:] if len(intervalos) > 1 else intervalos

        bps_list = [iv["sum"]["bits_per_second"] for iv in intervalos_limpios]
        goodput_mbps = float(np.mean(bps_list) / 1e6)

        if len(bps_list) > 2 and np.std(bps_list) > 0:
            ac_lag1 = float(np.corrcoef(bps_list[:-1], bps_list[1:])[0, 1])
        else:
            ac_lag1 = 0.0

        dur_real = sum(iv["sum"]["seconds"] for iv in intervalos_limpios)
        for cont, cstat in meta.get("cpu_cgroup_delta", {}).items():
            uso_cpu = (cstat.get("usage_usec", 0) / 1e6) / max(params["duracion_s"], 1)
            if uso_cpu > 0.95:
                alertas_cordura.append(f"{meta['corrida']}: CPU elevada en {cont} ({uso_cpu:.2f})")

        registros.append({
            "rtt_ms": rtt_ms,
            "loss_pct": p_loss,
            "algo": algo,
            "rep": rep,
            "goodput_mbps": goodput_mbps,
            "ac_lag1": ac_lag1,
            "dur_util_s": dur_real,
        })

    if not registros:
        print("No se encontraron corridas válidas para analizar.")
        return

    rtts = sorted(list({r["rtt_ms"] for r in registros}))
    losses = sorted(list({r["loss_pct"] for r in registros}))
    algos = [a for a in ["reno", "cubic", "bbr"] if any(r["algo"] == a for r in registros)]

    resumen = {}
    for rtt in rtts:
        for algo in algos:
            for p in losses:
                vals = [r["goodput_mbps"] for r in registros if r["rtt_ms"] == rtt and r["algo"] == algo and r["loss_pct"] == p]
                if vals:
                    media, std, ic95 = ic_media_t(vals)
                    resumen[(rtt, algo, p)] = {
                        "media": media,
                        "std": std,
                        "ic95": ic95,
                        "n": len(vals),
                        "mathis": modelo_mathis_mbps(rtt, p),
                        "pftk": modelo_pftk_mbps(rtt, p),
                        "cubic_teo": modelo_cubic_rfc9438_mbps(rtt, p),
                        "bbr_teo": modelo_bbr_mbps(rtt, p),
                    }

    pendientes = {}
    for rtt in rtts:
        for algo in algos:
            p_validos = []
            x_validos = []
            for p in losses:
                if (rtt, algo, p) in resumen:
                    x_med = resumen[(rtt, algo, p)]["media"]
                    if algo == "bbr" or x_med < 0.85 * ETA_C_MBPS:
                        p_validos.append(p)
                        x_validos.append(x_med)
            m, m_ic, r2 = regresion_loglog(np.array(p_validos), np.array(x_validos))
            pendientes[(rtt, algo)] = {
                "pendiente": m,
                "ic95": m_ic,
                "r2": r2,
                "puntos_usados": p_validos,
            }

    lineas_md = [
        "# Resumen Estadístico — Experimento T4 (Pérdida Aleatoria)\n",
        f"- **Capacidad cuello de botella ($C$):** {C_MBPS} Mbit/s | **$MSS$:** {int(MSS_BYTES)} B | **$\\eta C$:** {ETA_C_MBPS:.2f} Mbit/s\n",
        "- **Transitorios descartados:** `-O 2` s de arranque + 1er intervalo post-`-O` (datos en vuelo).\n",
        "- **Estadístico:** Media entre repeticiones independientes con IC 95% ($t$ de Student).\n\n",
        "## 1. Tabla de Goodput Medido vs. Modelos Analíticos (Mbit/s)\n",
        "| RTT (ms) | Algoritmo | p (%) | Medido ± IC95% | Mathis | PFTK | Cubic (RFC 9438) | BBR $\\eta C(1-p)$ |\n",
        "|---:|:---|---:|---:|---:|---:|---:|---:|\n",
    ]

    for rtt in rtts:
        for algo in algos:
            for p in losses:
                if (rtt, algo, p) not in resumen:
                    continue
                d = resumen[(rtt, algo, p)]
                lineas_md.append(
                    f"| {rtt} | {algo.upper()} | {p} | "
                    f"{d['media']:.2f} ± {d['ic95']:.2f} | "
                    f"{d['mathis']:.2f} | {d['pftk']:.2f} | {d['cubic_teo']:.2f} | {d['bbr_teo']:.2f} |\n"
                )

    lineas_md.extend([
        "\n## 2. Pendientes en Escala Log-Log ($\\log x$ vs. $\\log p$)\n",
        "| RTT (ms) | Algoritmo | Pendiente Empírica ± IC95% | $R^2$ | Pendiente Teórica | Rango $p$ ajustado (%) |\n",
        "|---:|:---|---:|---:|---:|:---|\n",
    ])

    teo_pend = {
        "reno": "-0.50 (Mathis) a -1.00 (PFTK timeouts)",
        "cubic": "-0.75 (región cúbica) / -0.50 (TCP-friendly)",
        "bbr": "~0.00 (insensible a pérdida moderada)",
    }

    for rtt in rtts:
        for algo in algos:
            pe = pendientes[(rtt, algo)]
            rango_str = ", ".join(str(x) for x in pe["puntos_usados"])
            lineas_md.append(
                f"| {rtt} | {algo.upper()} | {pe['pendiente']:.3f} ± {pe['ic95']:.3f} | "
                f"{pe['r2']:.3f} | {teo_pend[algo]} | [{rango_str}] |\n"
            )

    if alertas_cordura:
        lineas_md.append("\n## Alertas de Cordura (CPU)\n")
        for a in alertas_cordura:
            lineas_md.append(f"- {a}\n")

    ruta_md = DIR_BASE / "resumen.md"
    ruta_md.write_text("".join(lineas_md))
    print(f"✔ Tabla y resumen guardados en: {ruta_md}")

    # =================================================================
    # GRÁFICO 1: GOODPUT VS PÉRDIDA (LEYENDA SUPERIOR COMPARTIDA)
    # =================================================================
    fig, axes = plt.subplots(1, len(rtts), figsize=(15, 6.5), sharey=True)
    if len(rtts) == 1:
        axes = [axes]

    colores_algo = {"reno": "#1f77b4", "cubic": "#ff7f0e", "bbr": "#2ca02c"}
    marcadores_algo = {"reno": "o", "cubic": "s", "bbr": "^"}
    p_grid = np.logspace(np.log10(min(losses)), np.log10(max(losses)), 200)

    for idx, rtt in enumerate(rtts):
        ax = axes[idx]
        ax.axhline(ETA_C_MBPS, color="gray", linestyle=":", linewidth=1.2, label=f"Capacidad $\\eta C$ ({ETA_C_MBPS:.1f} Mbps)")

        ax.plot(p_grid, [modelo_mathis_mbps(rtt, pv) for pv in p_grid], "--", color="#1f77b4", alpha=0.6, label="Teórico: Mathis ($p^{-0.5}$)")
        ax.plot(p_grid, [modelo_pftk_mbps(rtt, pv) for pv in p_grid], ":", color="#9467bd", linewidth=2, label="Teórico: PFTK (con TO)")
        ax.plot(p_grid, [modelo_cubic_rfc9438_mbps(rtt, pv) for pv in p_grid], "--", color="#ff7f0e", alpha=0.6, label="Teórico: Cubic RFC 9438")
        ax.plot(p_grid, [modelo_bbr_mbps(rtt, pv) for pv in p_grid], "--", color="#2ca02c", alpha=0.6, label="Teórico: BBR $\\eta C(1-p)$")

        for algo in algos:
            xs, ys, yerrs = [], [], []
            for p in losses:
                if (rtt, algo, p) in resumen:
                    xs.append(p)
                    ys.append(resumen[(rtt, algo, p)]["media"])
                    yerrs.append(resumen[(rtt, algo, p)]["ic95"])
            ax.errorbar(
                xs, ys, yerr=yerrs,
                fmt=marcadores_algo[algo] + "-", color=colores_algo[algo],
                capsize=4, linewidth=2, markersize=6,
                label=f"Medido: {algo.upper()} (± IC 95%)"
            )

        ax.set_xscale("log")
        ax.set_xticks(losses)
        ax.set_xticklabels([f"{p}%" for p in losses])
        ax.set_title(f"RTT = {rtt} ms (Buffer = BDP)")
        ax.set_xlabel("Tasa de pérdida aleatoria $p$ (escala log)")
        if idx == 0:
            ax.set_ylabel("Goodput (Mbit/s)")
        ax.grid(True, which="both", linestyle="--", alpha=0.5)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.suptitle("Experimento T4: Caída de Goodput con la Pérdida Aleatoria", fontsize=13, fontweight="bold", y=0.98)
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.93), ncol=4, fontsize=8.5, frameon=True)
    plt.tight_layout(rect=[0, 0, 1, 0.82])
    ruta_g1 = DIR_BASE / "T4_Perdida_Goodput.png"
    plt.savefig(ruta_g1, dpi=300)
    plt.close()
    print(f"✔ Gráfico 1 guardado en: {ruta_g1}")

    # =================================================================
    # GRÁFICO 2: ESCALA LOG-LOG Y PENDIENTES (LEYES DE POTENCIA)
    # =================================================================
    fig, axes = plt.subplots(1, len(rtts), figsize=(15, 6), sharey=True)
    if len(rtts) == 1:
        axes = [axes]

    for idx, rtt in enumerate(rtts):
        ax = axes[idx]
        for algo in algos:
            xs, ys = [], []
            for p in losses:
                if (rtt, algo, p) in resumen:
                    xs.append(p)
                    ys.append(resumen[(rtt, algo, p)]["media"])
            pe = pendientes[(rtt, algo)]
            ax.loglog(
                xs, ys,
                marker=marcadores_algo[algo], linestyle="-",
                color=colores_algo[algo], linewidth=2,
                label=f"{algo.upper()} (pendiente = {pe['pendiente']:.2f} ± {pe['ic95']:.2f})"
            )

        p_ref = np.array([0.1, 5.0])
        ax.loglog(p_ref, 30.0 * (p_ref / 0.1) ** (-0.5), "k--", alpha=0.5, label="Ref. pendiente -0.50 (Mathis)")
        ax.loglog(p_ref, 30.0 * (p_ref / 0.1) ** (-0.75), "k:", alpha=0.6, label="Ref. pendiente -0.75 (Cubic)")

        ax.set_title(f"Regresión Log-Log (RTT = {rtt} ms)")
        ax.set_xlabel("Tasa de pérdida $p$ (%)")
        if idx == 0:
            ax.set_ylabel("Goodput $x$ (Mbit/s)")
        ax.grid(True, which="both", linestyle="--", alpha=0.5)
        ax.legend(fontsize=8.5, loc="lower left")

    fig.suptitle("Experimento T4: Leyes de Potencia ($\\log x$ vs. $\\log p$)", fontsize=13, fontweight="bold")
    plt.tight_layout()
    ruta_g2 = DIR_BASE / "T4_Perdida_LogLog.png"
    plt.savefig(ruta_g2, dpi=300)
    plt.close()
    print(f"✔ Gráfico 2 guardado en: {ruta_g2}")


if __name__ == "__main__":
    main()
