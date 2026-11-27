import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

DIR_BASE = Path("results/H3")


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


def simular_ps(a_s, s_s, c0_ms=0.0):
    n = len(a_s)
    t_ms = np.empty(n, dtype=float)
    activos = []  # [trabajo_restante_s, indice_pedido]
    idx_arr = 0
    t = float(a_s[0]) if n > 0 else 0.0

    while idx_arr < n or activos:
        if not activos:
            t = float(a_s[idx_arr])
            activos.append([float(s_s[idx_arr]), idx_arr])
            idx_arr += 1
            continue

        m = len(activos)
        min_rem = min(item[0] for item in activos)
        dt_fin = min_rem * m

        if idx_arr < n:
            dt_arr = float(a_s[idx_arr]) - t
            if dt_arr < 0.0:
                dt_arr = 0.0
        else:
            dt_arr = float("inf")

        if dt_arr <= dt_fin:
            trabajo = dt_arr / m
            for item in activos:
                item[0] -= trabajo
            t += dt_arr
            activos.append([float(s_s[idx_arr]), idx_arr])
            idx_arr += 1
        else:
            trabajo = min_rem
            t += dt_fin
            nuevos_activos = []
            for item in activos:
                rem = item[0] - trabajo
                if rem <= 1e-12:
                    k = item[1]
                    t_ms[k] = (t - float(a_s[k])) * 1000.0 + c0_ms
                else:
                    nuevos_activos.append([rem, item[1]])
            activos = nuevos_activos

    return t_ms


def validar_simulador_ps_sintetico():
    rng = np.random.default_rng(77777)
    n = 120000
    es_s = 0.010
    rho = 0.7
    lam = rho / es_s
    a_s = np.cumsum(rng.exponential(1.0 / lam, size=n))
    t_teo = (es_s / (1.0 - rho)) * 1000.0

    # M/M/1-PS
    s_exp = rng.exponential(es_s, size=n)
    t_sim_exp = float(np.mean(simular_ps(a_s, s_exp, 0.0)[3000:]))
    err_exp = ((t_sim_exp - t_teo) / t_teo) * 100.0

    # M/H2/1-PS (insensibilidad teorica: misma media que M/M/1-PS)
    c2 = 5.0
    p1 = 0.5 * (1.0 + math.sqrt((c2 - 1.0) / (c2 + 1.0)))
    m1 = es_s / (2.0 * p1)
    m2 = es_s / (2.0 * (1.0 - p1))
    s_h2 = np.where(
        rng.random(size=n) < p1,
        rng.exponential(m1, size=n),
        rng.exponential(m2, size=n),
    )
    t_sim_h2 = float(np.mean(simular_ps(a_s, s_h2, 0.0)[3000:]))
    err_h2 = ((t_sim_h2 - t_teo) / t_teo) * 100.0

    return {
        "exp": (t_teo, t_sim_exp, err_exp),
        "h2": (t_teo, t_sim_h2, err_h2),
    }


def main():
    f_cal = DIR_BASE / "calibracion_red.json"
    if not DIR_BASE.exists() or not f_cal.exists():
        print("Faltan datos o calibracion_red.json en results/H3.")
        return

    cal = json.loads(f_cal.read_text())
    c0_ms = float(cal["c0_medido_ms"])
    s0_ms = float(cal["s0_medido_ms"])
    rtt_ms = float(cal["rtt_ping"]["rtt_avg_ms"])
    val_sin = validar_simulador_ps_sintetico()

    filas = []
    trazas_cond = []

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
        # En PS, el servicio puro S = D + s0 (cpu_pedida + s0)
        s_s = (df["cpu_pedida_ms"].to_numpy(dtype=float) + s0_ms) / 1000.0
        t_han_arr = df["t_handler_ms"].to_numpy(dtype=float)
        t_tot_arr = df["latencia_total_ms"].to_numpy(dtype=float)
        t_ext_arr = t_tot_arr - t_han_arr

        span_s = float(a_s[-1] - a_s[0])
        lam_med = (len(a_s) - 1) / span_s if span_s > 0 else 0.0
        es_med = float(np.mean(s_s))
        rho_med = lam_med * es_med

        # Modelo cerrado M/G/1-PS: E[T] = E[S] / (1 - rho) + c0
        et_han_teo_ms = (es_med / max(1e-6, 1.0 - rho_med)) * 1000.0
        et_tot_teo_ms = et_han_teo_ms + c0_ms

        # Simulacion PS con traza
        t_sim_tot_arr = simular_ps(a_s, s_s, c0_ms)
        et_sim_tot_ms = float(np.mean(t_sim_tot_arr))
        et_sim_han_ms = et_sim_tot_ms - c0_ms

        et_tot_ms, _, ic_lote_ms = batch_means_ic(t_tot_arr, n_lotes=20)
        et_han_ms = float(np.mean(t_han_arr))
        et_ext_ms = float(np.mean(t_ext_arr))
        r1 = autocorr_lag1(t_tot_arr)

        inter_a = np.diff(a_s)
        ks_stat, ks_p = stats.kstest(
            inter_a, "expon", args=(0.0, 1.0 / lam_med)
        )
        atraso_m = float(df["atraso_envio_ms"].mean())

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
            "rho_med": rho_med,
            "rho_cg": rho_cg,
            "throttled": thr_cnt,
            "et_tot_ms": et_tot_ms,
            "ic_lote_ms": ic_lote_ms,
            "et_sim_tot_ms": et_sim_tot_ms,
            "et_tot_teo_ms": et_tot_teo_ms,
            "et_han_ms": et_han_ms,
            "et_sim_han_ms": et_sim_han_ms,
            "et_han_teo_ms": et_han_teo_ms,
            "et_ext_ms": et_ext_ms,
            "r1_lag1": r1,
            "atraso_m_ms": atraso_m,
            "ks_stat": float(ks_stat),
            "ks_p": float(ks_p),
        })

        if dist in ("exp", "h2"):
            sub_c = pd.DataFrame({
                "rho_obj": rho_obj,
                "dist": dist,
                "s_ms": s_s * 1000.0,
                "t_tot_ms": t_tot_arr,
                "t_han_ms": t_han_arr,
            })
            trazas_cond.append(sub_c)

    if not filas:
        print("Sin corridas validas en results/H3.")
        return

    df_res = pd.DataFrame(filas)
    df_cond = pd.concat(trazas_cond, ignore_index=True) if trazas_cond else None
    dists = ["det", "exp", "h2"]
    rhos = sorted(df_res["rho_obj"].unique())

    # Resumen Markdown
    t_exp_teo, t_exp_sim, e_exp = val_sin["exp"]
    t_h2_teo, t_h2_sim, e_h2 = val_sin["h2"]
    md = [
        "# Resumen Estadistico - Experimento H3 (M/G/1-PS)\n\n",
        "## 1. Calibracion Base y Validacion Simulador PS\n",
        f"- **RTT real medido (ping):** `{rtt_ms:.3f} ms`\n",
        f"- **Servicio vacio PS (s0):** `{s0_ms:.4f} ms`\n",
        f"- **Latencia externa base (c0):** `{c0_ms:.4f} ms`\n",
        f"- **Validacion PS M/M/1 (rho=0.7):** Teo=`{t_exp_teo:.2f} ms`, ",
        f"Sim=`{t_exp_sim:.2f} ms` (`{e_exp:+.2f}%`)\n",
        f"- **Validacion PS M/H2/1 (rho=0.7):** Teo=`{t_h2_teo:.2f} ms`, ",
        f"Sim=`{t_h2_sim:.2f} ms` (`{e_h2:+.2f}%`)\n\n",
        "## 2. Insensibilidad de E[T] y Descomposicion Handler vs. Total\n",
        "| Dist | rho_obj | rho_med | rho_cg | E[T] Total Med +- IC95% (ms) | ",
        "E[T] Sim PS (ms) | E[T] Teo PS (ms) | T_handler Med (ms) | ",
        "T_handler Teo (ms) | T_externo Med (ms) |\n",
        "|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n",
    ]

    for rho in rhos:
        for dist in dists:
            sub = df_res[
                (df_res["dist"] == dist) & (df_res["rho_obj"] == rho)
            ]
            if sub.empty:
                continue
            et_m, _, et_ic = ic_t_95(sub["et_tot_ms"])
            et_s = sub["et_sim_tot_ms"].mean()
            et_t = sub["et_tot_teo_ms"].mean()
            th_m = sub["et_han_ms"].mean()
            th_t = sub["et_han_teo_ms"].mean()
            te_m = sub["et_ext_ms"].mean()
            rm = sub["rho_med"].mean()
            rc = sub["rho_cg"].mean()
            md.append(
                f"| {dist} | {rho:.1f} | {rm:.3f} | {rc:.3f} | "
                f"{et_m:.2f} +- {et_ic:.2f} | {et_s:.2f} | {et_t:.2f} | "
                f"{th_m:.2f} | {th_t:.2f} | {te_m:.2f} |\n"
            )

    md.extend([
        "\n## 3. Pendiente Empirica E[T | S=s] vs. Teorica 1/(1 - rho)\n",
        "| rho_obj | rho_med | Pendiente Teorica 1/(1-rho) | ",
        "Pendiente Total Medida | Pendiente Handler Medida | ",
        "KS D_stat | Atraso envio (ms) | Autocorr r1 |\n",
        "|:---|---:|---:|---:|---:|---:|---:|---:|\n",
    ])
    for rho in rhos:
        sub_r = df_res[df_res["rho_obj"] == rho]
        rm = sub_r["rho_med"].mean()
        pend_teo = 1.0 / (1.0 - rm)
        ks_d = sub_r["ks_stat"].mean()
        at_m = sub_r["atraso_m_ms"].mean()
        r1_m = sub_r["r1_lag1"].mean()

        if df_cond is not None:
            sc = df_cond[df_cond["rho_obj"] == rho]
            reg_tot = stats.linregress(sc["s_ms"], sc["t_tot_ms"])
            reg_han = stats.linregress(sc["s_ms"], sc["t_han_ms"])
            p_tot = f"{reg_tot.slope:.2f}"
            p_han = f"{reg_han.slope:.2f}"
        else:
            p_tot, p_han = "-", "-"

        md.append(
            f"| {rho:.1f} | {rm:.3f} | {pend_teo:.2f} | {p_tot} | "
            f"{p_han} | {ks_d:.4f} | {at_m:.3f} | {r1_m:.3f} |\n"
        )

    (DIR_BASE / "resumen.md").write_text("".join(md))
    print(f"[1/3] Resumen guardado en {DIR_BASE / 'resumen.md'}")

    # GRAFICO 1: Insensibilidad de E[T] y Linealidad de E[T|S=s] (sin barras de IC)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.5, 5.3))
    col_d = {"det": "#1f77b4", "exp": "#ff7f0e", "h2": "#d62728"}
    mar_d = {"det": "o", "exp": "s", "h2": "^"}
    nom_d = {
        "det": "det (cs^2=0)",
        "exp": "exp (cs^2=1)",
        "h2": "H2 (cs^2=5)",
    }

    for dist in dists:
        xs, ym, ys = [], [], []
        for rho in rhos:
            sub = df_res[
                (df_res["dist"] == dist) & (df_res["rho_obj"] == rho)
            ]
            xs.append(sub["rho_med"].mean())
            ym.append(sub["et_tot_ms"].mean())
            ys.append(sub["et_sim_tot_ms"].mean())

        ax1.plot(
            xs, ym, mar_d[dist] + "-",
            color=col_d[dist], lw=2,
            label=f"Medido {nom_d[dist]}"
        )
        ax1.plot(
            xs, ys, "--", color=col_d[dist], lw=1.2, alpha=0.8,
            label=f"Sim PS {dist}"
        )

    rho_grid = np.linspace(0.48, 0.92, 100)
    es_prom = df_res["es_ms"].mean()
    et_grid = es_prom / (1.0 - rho_grid) + c0_ms
    ax1.plot(
        rho_grid, et_grid, "k:", lw=2.2,
        label="Modelo PS: E[S]/(1-rho) + c0"
    )
    ax1.set_ylim(bottom=0)
    ax1.set_title("Insensibilidad: E[T] Total vs. Utilizacion (rho)")
    ax1.set_xlabel("Utilizacion medida (rho)")
    ax1.set_ylabel("Tiempo medio de respuesta E[T] (ms)")
    ax1.grid(True, ls="--", alpha=0.5)
    ax1.legend(fontsize=8, ncol=2)

    # Panel derecho: E[T | S=s] vs s con pendiente 1/(1-rho)
    col_r = {0.5: "#1f77b4", 0.7: "#ff7f0e", 0.9: "#d62728"}
    bins = np.linspace(0.5, 45.0, 10)
    s_line = np.linspace(0.5, 45.0, 50)

    for rho in rhos:
        sub_r = df_res[df_res["rho_obj"] == rho]
        rm = sub_r["rho_med"].mean()
        ax2.plot(
            s_line, s_line / (1.0 - rm) + c0_ms, "--",
            color=col_r[rho], lw=1.6,
            label=f"Teo rho={rho} (pend={1.0/(1.0-rm):.1f})"
        )
        if df_cond is not None:
            sc = df_cond[df_cond["rho_obj"] == rho].copy()
            sc["bin"] = pd.cut(sc["s_ms"], bins=bins)
            bx, by = [], []
            for _, grp in sc.groupby("bin", observed=False):
                if len(grp) >= 25:
                    bx.append(grp["s_ms"].mean())
                    by.append(grp["t_tot_ms"].mean())
            ax2.plot(
                bx, by, "o-", color=col_r[rho], lw=2,
                label=f"Medido rho={rho}"
            )

    ax2.set_ylim(bottom=0)
    ax2.set_title("Linealidad Condicional: E[T | S=s] vs. Servicio s")
    ax2.set_xlabel("Tiempo de servicio pedido S = D + s0 (ms)")
    ax2.set_ylabel("Tiempo de respuesta E[T | S=s] (ms)")
    ax2.grid(True, ls="--", alpha=0.5)
    ax2.legend(fontsize=8)

    plt.tight_layout()
    g1 = DIR_BASE / "H3_1_Insensibilidad_y_Pendiente.png"
    plt.savefig(g1, dpi=300)
    plt.close()
    print(f"[2/3] Grafico guardado en {g1}")

    # GRAFICO 2: Donde se va el tiempo (Handler interno vs Externo)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.5, 5.3))

    for dist in dists:
        xs, yh, ys_h, yext = [], [], [], []
        for rho in rhos:
            sub = df_res[
                (df_res["dist"] == dist) & (df_res["rho_obj"] == rho)
            ]
            xs.append(sub["rho_med"].mean())
            yh.append(sub["et_han_ms"].mean())
            ys_h.append(sub["et_sim_han_ms"].mean())
            yext.append(sub["et_ext_ms"].mean())

        ax1.plot(
            xs, yh, mar_d[dist] + "-", color=col_d[dist], lw=2,
            label=f"Handler Medido ({dist})"
        )
        ax1.plot(
            xs, ys_h, "--", color=col_d[dist], lw=1.2, alpha=0.8,
            label=f"Sim PS Handler ({dist})"
        )
        ax2.plot(
            xs, yext, mar_d[dist] + "-", color=col_d[dist], lw=2,
            label=f"Fuera del Handler ({dist})"
        )

    ax1.plot(
        rho_grid, es_prom / (1.0 - rho_grid), "k:", lw=2.2,
        label="Modelo PS puro: E[S]/(1-rho)"
    )
    ax1.set_ylim(bottom=0)
    ax1.set_title("Tiempo Dentro del Handler (Threadpool PS)")
    ax1.set_xlabel("Utilizacion medida (rho)")
    ax1.set_ylabel("Tiempo medio en handler (ms)")
    ax1.grid(True, ls="--", alpha=0.5)
    ax1.legend(fontsize=8, ncol=2)

    ax2.axhline(
        c0_ms, color="k", ls=":", lw=2.0,
        label=f"c0 base en vacio ({c0_ms:.2f} ms)"
    )
    ax2.set_ylim(bottom=0)
    ax2.set_title("Tiempo Fuera del Handler (T_total - T_handler)")
    ax2.set_xlabel("Utilizacion medida (rho)")
    ax2.set_ylabel("Demora externa al handler (ms)")
    ax2.grid(True, ls="--", alpha=0.5)
    ax2.legend(fontsize=8.5)

    plt.tight_layout()
    g2 = DIR_BASE / "H3_2_Descomposicion_Handler_vs_Total.png"
    plt.savefig(g2, dpi=300)
    plt.close()
    print(f"[3/3] Grafico guardado en {g2}")


if __name__ == "__main__":
    main()
