import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

DIR_BASE = Path("results/H4")
MSS = 1448.0
HDR_TRAMA = 66.0


def ic_t(x, conf=0.95):
    """Media y semiancho del IC t de Student (repeticiones independientes)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    m = float(np.mean(x))
    if n < 2:
        return m, float("nan")
    h = stats.t.ppf(0.5 + conf / 2.0, n - 1) * np.std(x, ddof=1) / np.sqrt(n)
    return m, float(h)


def bytes_en_cable(r_bytes, h_http):
    """Bytes que ocupa una respuesta de R bytes en el enlace (tramas de 1514 B)."""
    carga = r_bytes + h_http
    tramas = np.ceil(carga / MSS)
    return carga + HDR_TRAMA * tramas


def simular_tandem_fifo(a_s, s_cpu_s, s_red_s, c0_ms=0.0):
    """Lindley en dos etapas: CPU (FIFO) -> enlace (FIFO)."""
    n = len(a_s)
    t_tot_ms = np.empty(n, dtype=float)
    t_etapa1_ms = np.empty(n, dtype=float)
    t_etapa2_ms = np.empty(n, dtype=float)

    fin_cpu = 0.0
    fin_red = 0.0

    for k in range(n):
        arr = float(a_s[k])
        ini_cpu = arr if arr > fin_cpu else fin_cpu
        fin_cpu = ini_cpu + float(s_cpu_s[k])

        ini_red = fin_cpu if fin_cpu > fin_red else fin_red
        fin_red = ini_red + float(s_red_s[k])

        t_etapa1_ms[k] = (fin_cpu - arr) * 1000.0
        t_etapa2_ms[k] = (fin_red - fin_cpu) * 1000.0
        t_tot_ms[k] = (fin_red - arr) * 1000.0 + c0_ms

    return t_tot_ms, t_etapa1_ms, t_etapa2_ms


def validar_simulador_tandem_sintetico():
    rng = np.random.default_rng(44444)
    n = 120000
    es1 = 0.010
    es2 = 0.012
    lam = 60.0
    rho1 = lam * es1
    rho2 = lam * es2

    a_s = np.cumsum(rng.exponential(1.0 / lam, size=n))
    s1 = rng.exponential(es1, size=n)
    s2 = rng.exponential(es2, size=n)

    t_jackson_ms = (es1 / (1.0 - rho1) + es2 / (1.0 - rho2)) * 1000.0
    t_sim, _, _ = simular_tandem_fifo(a_s, s1, s2, 0.0)
    t_sim_ms = float(np.mean(t_sim[3000:]))
    err_pct = ((t_sim_ms - t_jackson_ms) / t_jackson_ms) * 100.0
    return t_jackson_ms, t_sim_ms, err_pct


def main():
    f_cal = DIR_BASE / "calibracion_tandem.json"
    if not DIR_BASE.exists() or not f_cal.exists():
        print("Faltan datos o calibracion_tandem.json en results/H4.")
        return

    cal = json.loads(f_cal.read_text())
    s0_cpu_ms = float(cal["s0_cpu_ms"])
    c0_puro_ms = float(cal["c0_puro_ms"])
    h_http = float(cal["h_http_bytes"])
    tasa_mbps = float(cal["tasa_enlace_mbps"])
    c_bytes_s = tasa_mbps * 1e6 / 8.0

    t_jk_sin, t_sm_sin, err_sin = validar_simulador_tandem_sintetico()

    filas = []
    for d in sorted(DIR_BASE.iterdir()):
        if not d.is_dir():
            continue
        f_meta = d / "meta.json"
        f_csv = d / "pedidos.csv"
        if not f_meta.exists() or not f_csv.exists():
            continue

        meta = json.loads(f_meta.read_text())
        df = pd.read_csv(f_csv)
        if len(df) < 50:
            continue

        par = meta["parametros"]
        rho_cpu_obj = float(par["rho_cpu_objetivo"])
        rho_red_obj = float(par["rho_red_objetivo"])
        rep = int(meta["repeticion"])

        a_s = df["t_envio_s"].to_numpy(dtype=float)
        s_cpu_s = (df["cpu_pedida_ms"].to_numpy(dtype=float) + s0_cpu_ms) / 1000.0
        r_arr = df["bytes_r"].to_numpy(dtype=float)
        # servicio del enlace = bytes reales en cable / capacidad
        s_red_s = bytes_en_cable(r_arr, h_http) / c_bytes_s

        t_han_arr = df["t_handler_ms"].to_numpy(dtype=float)
        t_tot_arr = df["latencia_total_ms"].to_numpy(dtype=float)
        t_ext_arr = t_tot_arr - t_han_arr

        span_s = float(a_s[-1] - a_s[0])
        lam_med = (len(a_s) - 1) / span_s if span_s > 0 else 0.0
        es_cpu_s = float(np.mean(s_cpu_s))
        es_red_s = float(np.mean(s_red_s))

        rho_cpu_med = lam_med * es_cpu_s
        rho_red_traza = lam_med * es_red_s   # desde bytes_r (modelo)

        dur_real = float(meta.get("duracion_real_s", 130.0))
        bytes_tc = float(meta.get("tc_delta", {}).get("bytes_enviados", 0))
        drops_tc = int(meta.get("tc_delta", {}).get("dropped", 0))
        # rho del enlace por el camino independiente: contador del tbf
        rho_red_tc = (
            (bytes_tc * 8.0) / (tasa_mbps * 1e6 * dur_real)
            if bytes_tc > 0
            else float("nan")
        )

        # Jackson: dos M/M/1 en serie
        t_jk_cpu_ms = (es_cpu_s / max(1e-6, 1.0 - rho_cpu_med)) * 1000.0
        wq_jk_cpu_ms = t_jk_cpu_ms - es_cpu_s * 1000.0
        t_jk_red_ms = (es_red_s / max(1e-6, 1.0 - rho_red_traza)) * 1000.0
        t_jk_tot_ms = t_jk_cpu_ms + t_jk_red_ms + c0_puro_ms

        # Simulacion de traza (Lindley, 2 etapas) con los bytes_r reales
        t_sim_tot, t_sim_e1, t_sim_e2 = simular_tandem_fifo(
            a_s, s_cpu_s, s_red_s, c0_puro_ms
        )

        cpu_api = meta.get("cpu_cgroup_delta", {}).get("api1", {})
        rho_cg = (cpu_api.get("usage_usec", 0) / 1e6) / dur_real
        inter_a = np.diff(a_s)
        ks_stat, _ = stats.kstest(inter_a, "expon", args=(0.0, 1.0 / lam_med))

        filas.append({
            "combo": f"({rho_cpu_obj:.1f}, {rho_red_obj:.1f})",
            "rho_cpu_obj": rho_cpu_obj,
            "rho_red_obj": rho_red_obj,
            "rep": rep,
            "rho_cpu_med": rho_cpu_med,
            "rho_cg": rho_cg,
            "rho_red_traza": rho_red_traza,
            "rho_red_tc": rho_red_tc,
            "drops_tc": drops_tc,
            "et_med_ms": float(np.mean(t_tot_arr)),
            "et_sim_ms": float(np.mean(t_sim_tot)),
            "et_jk_ms": t_jk_tot_ms,
            "serv_cpu_med_ms": float(np.mean(t_han_arr)),
            "ext_med_ms": float(np.mean(t_ext_arr)),
            "serv_cpu_jk_ms": es_cpu_s * 1000.0,
            "wq_cpu_jk_ms": wq_jk_cpu_ms,
            "red_jk_ms": t_jk_red_ms,
            "e1_sim_ms": float(np.mean(t_sim_e1)),
            "e2_sim_ms": float(np.mean(t_sim_e2)),
            "ks_stat": float(ks_stat),
        })

    if not filas:
        print("Sin corridas validas en results/H4.")
        return

    df_res = pd.DataFrame(filas)
    combos_ord = ["(0.5, 0.5)", "(0.5, 0.7)", "(0.7, 0.5)", "(0.7, 0.7)"]

    md = [
        "# Resumen Estadistico - Experimento H4 (Tandem CPU -> Enlace)\n\n",
        "## 1. Calibracion y Validacion del Simulador de Dos Etapas\n",
        f"- **Capacidad enlace cuello de botella (tbf):** `{tasa_mbps:.1f} Mbit/s` "
        f"(burst `{cal.get('burst_tbf', '?')}`)\n",
        f"- **Servicio vacio CPU (s0_cpu):** `{s0_cpu_ms:.4f} ms`\n",
        f"- **Latencia pura sin cola (c0_puro):** `{c0_puro_ms:.4f} ms`\n",
        f"- **Overhead HTTP+ACKs en cable por pedido (H):** `{h_http:.0f} B`\n",
        f"- **Pendiente bytes_cable vs R:** medida `{cal['pendiente_wire_medida']:.4f}`, "
        f"teorica `{cal['pendiente_wire_teorica']:.4f}`\n",
        f"- **Descartes en buffer tbf (total corridas):** `{int(df_res['drops_tc'].sum())}`\n",
        f"- **Validacion sintetica Tandem vs. Jackson:** "
        f"Jackson=`{t_jk_sin:.2f} ms`, Sim=`{t_sm_sin:.2f} ms` (`{err_sin:+.2f}%`)\n\n",
        "## 2. Utilizaciones (objetivo vs. dos caminos de medicion)\n",
        "| (rho_cpu, rho_red) | rho_cpu lambda*E[S] | rho_cpu cgroup | ",
        "rho_red bytes_r (modelo) | rho_red tc (cable) |\n",
        "|:---|---:|---:|---:|---:|\n",
    ]
    for cb in combos_ord:
        sub = df_res[df_res["combo"] == cb]
        if sub.empty:
            continue
        md.append(
            f"| {cb} | {sub['rho_cpu_med'].mean():.3f} | {sub['rho_cg'].mean():.3f} | "
            f"{sub['rho_red_traza'].mean():.3f} | {sub['rho_red_tc'].mean():.3f} |\n"
        )

    md.extend([
        "\n## 3. Comparacion Total: Medido vs. Simulacion 2 Etapas vs. Jackson\n",
        "IC 95% t de Student entre repeticiones (n por combo indicado).\n\n",
        "| (rho_cpu, rho_red) | n | E[T] Medido (ms) | E[T] Sim 2 Etapas (ms) | ",
        "E[T] Jackson (ms) | Error vs Jackson (%) | Error vs Sim (%) |\n",
        "|:---|---:|---:|---:|---:|---:|---:|\n",
    ])
    for cb in combos_ord:
        sub = df_res[df_res["combo"] == cb]
        if sub.empty:
            continue
        t_med, h_med = ic_t(sub["et_med_ms"])
        t_sim, h_sim = ic_t(sub["et_sim_ms"])
        t_jk = sub["et_jk_ms"].mean()
        err_jk = ((t_med - t_jk) / t_jk) * 100.0
        err_sm = ((t_med - t_sim) / t_sim) * 100.0
        md.append(
            f"| {cb} | {len(sub)} | {t_med:.2f} ± {h_med:.2f} | "
            f"{t_sim:.2f} ± {h_sim:.2f} | {t_jk:.2f} | "
            f"{err_jk:+.2f}% | {err_sm:+.2f}% |\n"
        )

    md.extend([
        "\n## 4. Descomposicion por Etapa\n",
        "Medido: servicio en handler y resto (cola CPU + enlace + red). ",
        "Sim/Jackson: tiempo en sistema de cada etapa (espera + servicio).\n\n",
        "| (rho_cpu, rho_red) | Handler Med (ms) | Resto Med (ms) | ",
        "Etapa 1 CPU Sim (ms) | Etapa 1 CPU Jackson (ms) | ",
        "Etapa 2 Enlace Sim (ms) | Etapa 2 Enlace Jackson (ms) |\n",
        "|:---|---:|---:|---:|---:|---:|---:|\n",
    ])
    for cb in combos_ord:
        sub = df_res[df_res["combo"] == cb]
        if sub.empty:
            continue
        cpu_jk_tot = sub["serv_cpu_jk_ms"].mean() + sub["wq_cpu_jk_ms"].mean()
        md.append(
            f"| {cb} | {sub['serv_cpu_med_ms'].mean():.2f} | "
            f"{sub['ext_med_ms'].mean():.2f} | "
            f"{sub['e1_sim_ms'].mean():.2f} | {cpu_jk_tot:.2f} | "
            f"{sub['e2_sim_ms'].mean():.2f} | {sub['red_jk_ms'].mean():.2f} |\n"
        )

    (DIR_BASE / "resumen.md").write_text("".join(md))
    print(f"[1/2] Resumen guardado en {DIR_BASE / 'resumen.md'}")

    # ---------------- GRAFICO ----------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.5, 5.3))
    x = np.arange(len(combos_ord))
    w = 0.26

    def media_ic(col):
        mh = [ic_t(df_res[df_res["combo"] == c][col]) for c in combos_ord]
        return [m for m, _ in mh], [0 if np.isnan(h) else h for _, h in mh]

    med_vals, med_err = media_ic("et_med_ms")
    sim_vals, sim_err = media_ic("et_sim_ms")
    jk_vals = [df_res[df_res["combo"] == c]["et_jk_ms"].mean() for c in combos_ord]

    b1 = ax1.bar(x - w, med_vals, width=w, yerr=med_err, capsize=3,
                 color="#1f77b4", label="Medido (Real)")
    b2 = ax1.bar(x, sim_vals, width=w, yerr=sim_err, capsize=3,
                 color="#ff7f0e", label="Simulacion 2 Etapas")
    b3 = ax1.bar(x + w, jk_vals, width=w, color="#2ca02c", label="Modelo Jackson")

    for barras in (b1, b2, b3):
        for bar in barras:
            h = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width() / 2.0, h + 1.0, f"{h:.1f}",
                     ha="center", va="bottom", fontsize=8)

    ax1.set_xticks(x)
    ax1.set_xticklabels(combos_ord)
    ax1.set_ylim(0, max(np.array(med_vals) + np.array(med_err)) * 1.2)
    ax1.set_title("Tandem CPU -> Enlace: E[T] Total (IC 95%)")
    ax1.set_xlabel("Combinacion de carga (rho_cpu, rho_red)")
    ax1.set_ylabel("Tiempo medio de respuesta E[T] (ms)")
    ax1.grid(True, axis="y", ls="--", alpha=0.5)
    ax1.legend(fontsize=8.5)

    # Panel derecho: descomposicion por etapa
    g = lambda col: np.array([df_res[df_res["combo"] == c][col].mean() for c in combos_ord])
    s_med, ext_med = g("serv_cpu_med_ms"), g("ext_med_ms")
    e1_sim, e2_sim = g("e1_sim_ms"), g("e2_sim_ms")
    e1_jk = g("serv_cpu_jk_ms") + g("wq_cpu_jk_ms")
    e2_jk = g("red_jk_ms")

    w2 = 0.26
    ax2.bar(x - w2, s_med, width=w2, color="#1f77b4", label="Handler (Medido)")
    ax2.bar(x - w2, ext_med, width=w2, bottom=s_med, color="#aec7e8",
            label="Resto: cola CPU + enlace + red (Medido)")
    ax2.bar(x, e1_sim, width=w2, color="#ff7f0e", label="Etapa 1 CPU (Sim)")
    ax2.bar(x, e2_sim, width=w2, bottom=e1_sim, color="#ffbb78", label="Etapa 2 Enlace (Sim)")
    ax2.bar(x + w2, e1_jk, width=w2, color="#2ca02c", label="Etapa 1 CPU (Jackson)")
    ax2.bar(x + w2, e2_jk, width=w2, bottom=e1_jk, color="#98df8a", label="Etapa 2 Enlace (Jackson)")

    ax2.set_xticks(x)
    ax2.set_xticklabels(combos_ord)
    ax2.set_ylim(0, max(s_med + ext_med) * 1.35)
    ax2.set_title("Descomposicion por etapa")
    ax2.set_xlabel("Combinacion de carga (rho_cpu, rho_red)")
    ax2.set_ylabel("Tiempo medio acumulado (ms)")
    ax2.grid(True, axis="y", ls="--", alpha=0.5)
    ax2.legend(fontsize=7.5, ncol=2)

    plt.tight_layout()
    g1 = DIR_BASE / "H4_1_Tandem_Jackson_vs_Medido.png"
    plt.savefig(g1, dpi=300)
    plt.close()
    print(f"[2/2] Grafico guardado en {g1}")


if __name__ == "__main__":
    main()
