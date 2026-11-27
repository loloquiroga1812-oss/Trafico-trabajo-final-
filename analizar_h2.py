import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

DIR_BASE = Path("results/H2")


def ic_t_95(valores):
    arr = np.asarray(valores, dtype=float)
    n = len(arr)
    media = float(np.mean(arr))
    if n < 2:
        return media, 0.0, 0.0
    std = float(np.std(arr, ddof=1))
    t_crit = float(stats.t.ppf(0.975, n - 1))
    margen = t_crit * (std / math.sqrt(n))
    return media, std, margen


def batch_means_ic(serie, n_lotes=20):
    arr = np.asarray(serie, dtype=float)
    n = len(arr)
    if n < n_lotes * 5:
        return ic_t_95(arr)
    tam = n // n_lotes
    recortada = arr[: tam * n_lotes].reshape(n_lotes, tam)
    medias_lotes = np.mean(recortada, axis=1)
    return ic_t_95(medias_lotes)


def autocorr_lag1(serie):
    arr = np.asarray(serie, dtype=float)
    if len(arr) < 3:
        return 0.0
    x0 = arr[:-1] - np.mean(arr[:-1])
    x1 = arr[1:] - np.mean(arr[1:])
    den = float(np.sqrt(np.sum(x0 ** 2) * np.sum(x1 ** 2)))
    return float(np.sum(x0 * x1) / den) if den > 0 else 0.0


def simular_lindley(a_s, s_s, c0_ms=0.0):
    n = len(a_s)
    t_ms = np.empty(n, dtype=float)
    libre = 0.0
    for k in range(n):
        ak = a_s[k]
        sk = s_s[k]
        libre = max(ak, libre) + sk
        t_ms[k] = (libre - ak) * 1000.0 + c0_ms
    return t_ms


def validar_simulador_sintetico():
    rng = np.random.default_rng(99999)
    n = 250000
    es_s = 0.010
    rho = 0.8
    lam = rho / es_s
    inter = rng.exponential(1.0 / lam, size=n)
    a_s = np.cumsum(inter)

    s_exp = rng.exponential(es_s, size=n)
    t_sim_mm1 = float(np.mean(simular_lindley(a_s, s_exp, 0.0)[5000:]))
    t_teo_mm1 = (es_s / (1.0 - rho)) * 1000.0
    err_mm1 = ((t_sim_mm1 - t_teo_mm1) / t_teo_mm1) * 100.0

    s_det = np.full(n, es_s, dtype=float)
    t_sim_md1 = float(np.mean(simular_lindley(a_s, s_det, 0.0)[5000:]))
    t_teo_md1 = es_s * (1.0 + (rho / (1.0 - rho)) * 0.5) * 1000.0
    err_md1 = ((t_sim_md1 - t_teo_md1) / t_teo_md1) * 100.0

    return {
        "mm1": (t_teo_mm1, t_sim_mm1, err_mm1),
        "md1": (t_teo_md1, t_sim_md1, err_md1),
    }


def main():
    f_cal = DIR_BASE / "calibracion_red.json"
    if not DIR_BASE.exists() or not f_cal.exists():
        print("Faltan datos o calibracion_red.json en results/H2.")
        return

    cal = json.loads(f_cal.read_text())
    c0_ms = float(cal["c0_medido_ms"])
    rtt_ms = float(cal["rtt_ping"]["rtt_avg_ms"])
    val_sin = validar_simulador_sintetico()

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
        rho_obj = float(par["rho_objetivo"])
        dist = par["dist_d"]
        rep = int(meta["repeticion"])

        a_s = df["t_envio_s"].to_numpy(dtype=float)
        s_s = df["t_handler_ms"].to_numpy(dtype=float) / 1000.0
        t_med_arr = df["latencia_total_ms"].to_numpy(dtype=float)

        span_s = float(a_s[-1] - a_s[0])
        lam_med = (len(a_s) - 1) / span_s if span_s > 0 else 0.0
        es_med = float(np.mean(s_s))
        es2_med = float(np.mean(s_s ** 2))
        var_s = max(0.0, es2_med - es_med ** 2)
        cs2_med = var_s / (es_med ** 2) if es_med > 0 else 0.0
        rho_med = lam_med * es_med

        # Pollaczek-Khinchine usando momentos medidos de S
        wq_pk_s = (lam_med * es2_med) / (2.0 * max(1e-6, 1.0 - rho_med))
        et_pk_ms = (es_med + wq_pk_s) * 1000.0 + c0_ms

        # Simulacion Lindley alimentada con envios y servicios medidos
        t_lin_arr = simular_lindley(a_s, s_s, c0_ms)
        et_lin_ms = float(np.mean(t_lin_arr))

        et_med_ms, _, ic_lote_ms = batch_means_ic(t_med_arr, n_lotes=20)
        r1 = autocorr_lag1(t_med_arr)

        inter_a = np.diff(a_s)
        ks_stat, ks_p = stats.kstest(
            inter_a, "expon", args=(0.0, 1.0 / lam_med)
        )
        atraso_m = float(df["atraso_envio_ms"].mean())
        atraso_p95 = float(np.percentile(df["atraso_envio_ms"], 95))

        cpu_api = meta.get("cpu_cgroup_delta", {}).get("api1", {})
        dur_real = float(meta.get("duracion_real_s", 130.0))
        rho_cg = (cpu_api.get("usage_usec", 0) / 1e6) / dur_real
        thr_cnt = int(cpu_api.get("nr_throttled", 0))

        filas.append({
            "rho_obj": rho_obj,
            "dist": dist,
            "rep": rep,
            "n_pedidos": len(df),
            "lam_med": lam_med,
            "es_ms": es_med * 1000.0,
            "cs2_med": cs2_med,
            "rho_med": rho_med,
            "rho_cg": rho_cg,
            "throttled": thr_cnt,
            "et_med_ms": et_med_ms,
            "ic_lote_ms": ic_lote_ms,
            "et_lin_ms": et_lin_ms,
            "et_pk_ms": et_pk_ms,
            "r1_lag1": r1,
            "atraso_m_ms": atraso_m,
            "atraso_p95_ms": atraso_p95,
            "ks_stat": float(ks_stat),
            "ks_p": float(ks_p),
        })

    if not filas:
        print("Sin corridas validas en results/H2.")
        return

    df_res = pd.DataFrame(filas)
    dists = ["det", "exp", "h2"]
    rhos = sorted(df_res["rho_obj"].unique())

    t_mm1_teo, t_mm1_sim, e_mm1 = val_sin["mm1"]
    t_md1_teo, t_md1_sim, e_md1 = val_sin["md1"]
    md = [
        "# Resumen Estadistico - Experimento H2 (M/G/1 FIFO, cs^2=5)\n\n",
        "## 1. Calibracion de Red y Validacion de Lindley\n",
        f"- **RTT real medido (ping):** `{rtt_ms:.3f} ms`\n",
        f"- **Servicio vacio (s0):** `{cal['s0_medido_ms']:.4f} ms`\n",
        f"- **Latencia externa total (c0 = RTT + HTTP):** `{c0_ms:.4f} ms`\n",
        f"- **Validacion Lindley M/M/1 (rho=0.8):** Teo=`{t_mm1_teo:.2f} ms`, ",
        f"Sim=`{t_mm1_sim:.2f} ms` (`{e_mm1:+.2f}%`)\n",
        f"- **Validacion Lindley M/D/1 (rho=0.8):** Teo=`{t_md1_teo:.2f} ms`, ",
        f"Sim=`{t_md1_sim:.2f} ms` (`{e_md1:+.2f}%`)\n\n",
        "## 2. Comparacion Triangulo: Medido vs. Lindley vs. P-K\n",
        "| Dist | rho_obj | rho_med | rho_cg | cs2_med | ",
        "E[T] Med +- IC95% (ms) | E[T] Lindley (ms) | E[T] P-K (ms) | ",
        "Err Med-Lin (%) | Err Med-PK (%) |\n",
        "|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n",
    ]

    for dist in dists:
        for rho in rhos:
            sub = df_res[
                (df_res["dist"] == dist) & (df_res["rho_obj"] == rho)
            ]
            if sub.empty:
                continue
            et_m, _, et_ic = ic_t_95(sub["et_med_ms"])
            et_l = sub["et_lin_ms"].mean()
            et_p = sub["et_pk_ms"].mean()
            rm = sub["rho_med"].mean()
            rc = sub["rho_cg"].mean()
            cs2 = sub["cs2_med"].mean()
            err_l = ((et_m - et_l) / et_l) * 100.0
            err_p = ((et_m - et_p) / et_p) * 100.0
            md.append(
                f"| {dist} | {rho:.1f} | {rm:.3f} | {rc:.3f} | "
                f"{cs2:.2f} | {et_m:.2f} +- {et_ic:.2f} | {et_l:.2f} | "
                f"{et_p:.2f} | {err_l:+.2f}% | {err_p:+.2f}% |\n"
            )

    md.extend([
        "\n## 3. Auditoria de Llegadas Poisson, Lotes y Autocorrelacion\n",
        "| Dist | rho_obj | Atraso envio med/p95 (ms) | KS D_stat | ",
        "Autocorr r1 | IC Lote 95% (ms) | Throttles |\n",
        "|:---|---:|---:|---:|---:|---:|---:|\n",
    ])
    for dist in dists:
        for rho in rhos:
            sub = df_res[
                (df_res["dist"] == dist) & (df_res["rho_obj"] == rho)
            ]
            if sub.empty:
                continue
            at_m = sub["atraso_m_ms"].mean()
            at_95 = sub["atraso_p95_ms"].mean()
            ks_d = sub["ks_stat"].mean()
            r1_m = sub["r1_lag1"].mean()
            ic_l = sub["ic_lote_ms"].mean()
            thr_s = int(sub["throttled"].sum())
            md.append(
                f"| {dist} | {rho:.1f} | {at_m:.3f} / {at_95:.3f} | "
                f"{ks_d:.4f} | {r1_m:.3f} | +- {ic_l:.2f} | {thr_s} |\n"
            )

    (DIR_BASE / "resumen.md").write_text("".join(md))
    print(f"[1/2] Resumen guardado en {DIR_BASE / 'resumen.md'}")

    # Unico grafico: Comparacion E[T] Medido vs Lindley vs Pollaczek-Khinchine
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 5.0))
    titulos = {
        "det": "Determinista (cs^2 = 0)",
        "exp": "Exponencial (cs^2 = 1)",
        "h2": "Hiperexponencial H2 (cs^2 = 5)",
    }
    for idx, dist in enumerate(dists):
        ax = axes[idx]
        xs, ym, ye, yl, yp = [], [], [], [], []
        for rho in rhos:
            sub = df_res[
                (df_res["dist"] == dist) & (df_res["rho_obj"] == rho)
            ]
            xs.append(sub["rho_med"].mean())
            m_val, _, ic_val = ic_t_95(sub["et_med_ms"])
            ym.append(m_val)
            ye.append(ic_val)
            yl.append(sub["et_lin_ms"].mean())
            yp.append(sub["et_pk_ms"].mean())

        ym_arr = np.asarray(ym, dtype=float)
        ye_arr = np.asarray(ye, dtype=float)
        ye_inf = np.minimum(ym_arr, ye_arr)

        ax.errorbar(
            xs, ym_arr, yerr=[ye_inf, ye_arr], fmt="o-", color="#1f77b4",
            capsize=4, lw=2, label="Medido (IC 95%)"
        )
        ax.plot(
            xs, yl, "s--", color="#2ca02c", lw=1.8,
            label="Simulacion Lindley (traza)"
        )
        ax.plot(
            xs, yp, "^:", color="#d62728", lw=1.8,
            label="Pollaczek-Khinchine"
        )
        ax.set_ylim(bottom=0)
        ax.set_title(titulos[dist])
        ax.set_xlabel("Utilizacion medida (rho)")
        if idx == 0:
            ax.set_ylabel("Tiempo medio de respuesta E[T] (ms)")
        ax.grid(True, ls="--", alpha=0.5)
        ax.legend(fontsize=8.5)

    plt.tight_layout()
    g1 = DIR_BASE / "H2_1_Triangulo_ET_vs_Rho.png"
    plt.savefig(g1, dpi=300)
    plt.close()
    print(f"[2/2] Grafico guardado en {g1}")


if __name__ == "__main__":
    main()
