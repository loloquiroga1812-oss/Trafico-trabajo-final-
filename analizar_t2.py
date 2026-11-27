import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

DIR_BASE = Path("results/T2")
CAPACIDAD_MBPS = 100.0
ETA = 1448.0 / 1514.0  # eficiencia util de trama Ethernet 1514 B con MSS 1448 B


def ic_t(x, conf=0.95):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    m = float(np.mean(x)) if n else float("nan")
    if n < 2:
        return m, float("nan")
    h = stats.t.ppf(0.5 + conf / 2.0, n - 1) * np.std(x, ddof=1) / np.sqrt(n)
    return m, float(h)


def regresion_loglog(x_vals, y_vals, conf=0.95):
    x_arr = np.asarray(x_vals, dtype=float)
    y_arr = np.asarray(y_vals, dtype=float)
    mask = (~np.isnan(x_arr)) & (~np.isnan(y_arr)) & (x_arr > 0) & (y_arr > 0)
    x_arr = x_arr[mask]
    y_arr = y_arr[mask]
    n = len(x_arr)
    if n < 3:
        return {
            "n": n,
            "pendiente": float("nan"),
            "ic_pendiente": float("nan"),
            "intercepto": float("nan"),
            "r2": float("nan"),
            "p_val": float("nan"),
            "mape_pct": float("nan"),
        }
    lx = np.log10(x_arr)
    ly = np.log10(y_arr)
    res = stats.linregress(lx, ly)
    t_crit = stats.t.ppf(0.5 + conf / 2.0, n - 2)
    ic_pend = t_crit * res.stderr
    mape = float(np.mean(np.abs((y_arr - x_arr) / x_arr)) * 100.0)
    return {
        "n": n,
        "pendiente": float(res.slope),
        "ic_pendiente": float(ic_pend),
        "intercepto": float(res.intercept),
        "r2": float(res.rvalue ** 2),
        "p_val": float(res.pvalue),
        "mape_pct": mape,
    }


def extraer_metricas_iperf(f_ip):
    try:
        data = json.loads(f_ip.read_text())
    except Exception as e:
        return None, f"JSON invalido: {e}"

    if "error" in data:
        return None, f"Error iperf3: {data['error']}"

    ivs_cli = data.get("intervals", [])
    srv_obj = data.get("server_output_json", {})
    if isinstance(srv_obj, str):
        try:
            srv_obj = json.loads(srv_obj)
        except Exception:
            srv_obj = {}
    ivs_srv = srv_obj.get("intervals", []) if isinstance(srv_obj, dict) else []

    # Descartar siempre el primer intervalo post -O (datos en vuelo)
    ivs_cli = ivs_cli[1:]
    ivs_srv = ivs_srv[1:]

    if len(ivs_cli) < 2:
        return None, f"Pocos intervalos ({len(ivs_cli)})"

    tiene_srv = len(ivs_srv) >= 2
    n = min(len(ivs_cli), len(ivs_srv)) if tiene_srv else len(ivs_cli)

    goodputs_mbps = []
    srtts_ms = []
    snd_wnds_kb = []
    cwnds_kb = []
    retrans = 0

    for i in range(n):
        bps = float(ivs_cli[i]["sum"]["bits_per_second"])
        goodputs_mbps.append(bps / 1e6)

        st_src = (
            ivs_srv[i]["streams"][0]
            if tiene_srv and ivs_srv[i].get("streams")
            else ivs_cli[i]["streams"][0]
        )
        if "rtt" in st_src:
            srtts_ms.append(float(st_src["rtt"]) / 1000.0)
        if "snd_wnd" in st_src:
            snd_wnds_kb.append(float(st_src["snd_wnd"]) / 1024.0)
        if "snd_cwnd" in st_src:
            cwnds_kb.append(float(st_src["snd_cwnd"]) / 1024.0)
        retrans += int(st_src.get("retransmits", 0))

    return {
        "goodput_mbps": float(np.mean(goodputs_mbps)),
        "srtt_ms": float(np.mean(srtts_ms)) if srtts_ms else float("nan"),
        "snd_wnd_kb": float(np.mean(snd_wnds_kb)) if snd_wnds_kb else float("nan"),
        "cwnd_kb": float(np.mean(cwnds_kb)) if cwnds_kb else float("nan"),
        "retransmits": retrans,
        "tiene_srv_json": tiene_srv,
    }, None


def main():
    if not DIR_BASE.exists():
        print("No existe el directorio results/T2.")
        return

    filas_fija = []
    filas_libre = []
    series_temporales = {}

    # 1. Leer bloque ventana fija
    for d in sorted((DIR_BASE / "fija").glob("*")):
        f_meta = d / "meta.json"
        f_ip = d / "iperf.json"
        if not f_meta.exists() or not f_ip.exists():
            continue
        meta = json.loads(f_meta.read_text())
        met, _ = extraer_metricas_iperf(f_ip)
        if met is None:
            continue

        par = meta["parametros"]
        cap_mbps = float(par["capacidad_mbps"])
        cap_bps_bytes = (cap_mbps * 1e6) / 8.0
        eta_c = ETA * cap_mbps
        w_kb = float(par["w_kb"])
        rtt_cfg = float(par["rtt_cfg_ms"])
        rtt_tot_cfg = rtt_cfg + float(meta.get("rtt_base_ms", 0.0))

        srtt_eff = met["srtt_ms"] if not np.isnan(met["srtt_ms"]) else rtt_tot_cfg
        w_ef_kb = met["snd_wnd_kb"] if not np.isnan(met["snd_wnd_kb"]) else w_kb

        bdp_kb = (cap_bps_bytes * (rtt_tot_cfg * 1e-3)) / 1024.0
        cola_kb = max(0.0, ((srtt_eff - rtt_tot_cfg) * 1e-3) * cap_bps_bytes / 1024.0)

        pred_cfg_mbps = (w_kb * 8192.0) / (rtt_tot_cfg * 1e-3) / 1e6
        pred_ef_cfg_mbps = (w_ef_kb * 8192.0) / (rtt_tot_cfg * 1e-3) / 1e6
        pred_ef_srtt_mbps = (w_ef_kb * 8192.0) / (srtt_eff * 1e-3) / 1e6

        filas_fija.append({
            "w_kb": w_kb,
            "rtt_cfg_ms": rtt_cfg,
            "rtt_tot_cfg_ms": rtt_tot_cfg,
            "algo": par["algoritmo"],
            "rep": int(par["repeticion"]),
            "eta_c_mbps": eta_c,
            "goodput_mbps": met["goodput_mbps"],
            "srtt_ms": srtt_eff,
            "snd_wnd_kb": w_ef_kb,
            "bdp_kb": bdp_kb,
            "w_sobre_bdp": w_ef_kb / bdp_kb if bdp_kb > 0 else float("nan"),
            "cola_kb": cola_kb,
            "cola_sobre_bdp": cola_kb / bdp_kb if bdp_kb > 0 else float("nan"),
            "pred_cfg_mbps": pred_cfg_mbps,
            "pred_ef_cfg_mbps": pred_ef_cfg_mbps,
            "pred_ef_srtt_mbps": pred_ef_srtt_mbps,
        })

    # 2. Leer bloque ventana libre con buffer = BDP
    for d in sorted((DIR_BASE / "libre_bdp").glob("*")):
        f_meta = d / "meta.json"
        f_ip = d / "iperf.json"
        f_cola = d / "cola.json"
        if not f_meta.exists() or not f_ip.exists():
            continue
        meta = json.loads(f_meta.read_text())
        met, _ = extraer_metricas_iperf(f_ip)
        if met is None:
            continue

        par = meta["parametros"]
        cap_mbps = float(par["capacidad_mbps"])
        eta_c = ETA * cap_mbps
        bdp_bytes = float(par["buffer_bytes"])
        rtt_cfg = float(par["rtt_cfg_ms"])
        rtt_tot_cfg = rtt_cfg + float(meta.get("rtt_base_ms", 0.0))
        srtt_eff = met["srtt_ms"] if not np.isnan(met["srtt_ms"]) else rtt_tot_cfg
        algo = par["algoritmo"]
        rep = int(par["repeticion"])

        cola_prom_b = float("nan")
        cola_max_b = float("nan")
        drops_tot = 0
        if f_cola.exists():
            muestras = json.loads(f_cola.read_text())
            omit_s = float(par["omit_s"])
            dur_s = float(par["duracion_s"])
            # Recortar arranque (-O) y los ultimos 0.6s antes del cierre de iperf3
            t_fin_util = omit_s + dur_s - 0.6
            muestras_est = [
                m for m in muestras if omit_s <= m["t_s"] <= t_fin_util
            ]
            if muestras_est:
                b_arr = np.array([m["backlog_bytes"] for m in muestras_est], dtype=float)
                t_arr = np.array([m["t_s"] - omit_s for m in muestras_est], dtype=float)
                cola_prom_b = float(np.mean(b_arr))
                cola_max_b = float(np.max(b_arr))
                drops_tot = int(muestras_est[-1]["drops"] - muestras_est[0]["drops"])

                clave = (rtt_cfg, algo)
                if clave not in series_temporales or rep == 1:
                    series_temporales[clave] = (t_arr, b_arr / bdp_bytes)

        filas_libre.append({
            "rtt_cfg_ms": rtt_cfg,
            "rtt_tot_cfg_ms": rtt_tot_cfg,
            "algo": algo,
            "rep": rep,
            "eta_c_mbps": eta_c,
            "goodput_mbps": met["goodput_mbps"],
            "util_pct": (met["goodput_mbps"] / eta_c) * 100.0,
            "srtt_ms": srtt_eff,
            "bdp_kb": bdp_bytes / 1024.0,
            "cola_prom_kb": cola_prom_b / 1024.0,
            "cola_pct_bdp": (cola_prom_b / bdp_bytes) * 100.0 if bdp_bytes > 0 else float("nan"),
            "cola_max_kb": cola_max_b / 1024.0,
            "retransmits": met["retransmits"],
            "drops_tbf": drops_tot,
        })

    df_fija = pd.DataFrame(filas_fija)
    df_libre = pd.DataFrame(filas_libre)

    # ---------------- RESUMEN MARKDOWN ----------------
    md = [
        "# Resumen Estadistico - Experimento T2 (Ventana y BDP)\n\n",
        "## 1. Parametros de Referencia\n",
        f"- **Capacidad bruta del enlace (C):** `{CAPACIDAD_MBPS:.1f} Mbit/s`\n",
        f"- **Eficiencia maxima de trama (eta = 1448/1514):** `{ETA:.5f}` -> **Techo util `eta*C`:** `{ETA * CAPACIDAD_MBPS:.2f} Mbit/s`\n\n",
    ]

    if not df_fija.empty:
        md.extend([
            "## 2. Verificacion de la Ventana Efectiva de Linux (`iperf3 -w`)\n",
            "| W pedida (kB) | Ventana efectiva `snd_wnd` ± IC 95% (kB) | Factor `W_ef / W_pedida` |\n",
            "|---:|---:|---:|\n",
        ])
        for w_kb in sorted(df_fija["w_kb"].unique()):
            sub_w = df_fija[df_fija["w_kb"] == w_kb]
            m_w, h_w = ic_t(sub_w["snd_wnd_kb"])
            md.append(f"| {w_kb:.0f} | {m_w:.1f} ± {h_w:.1f} | {m_w / w_kb:.2f}x |\n")

        reg_lim = df_fija[df_fija["pred_ef_srtt_mbps"] < 0.85 * df_fija["eta_c_mbps"]].copy()
        md.extend([
            "\n## 3. Regresion Lineal Log-Log en la Region Limitada por Ventana\n",
            f"Puntos analizados donde `W_ef / srtt < 0.85 * eta * C` (`n = {len(reg_lim)}` corridas). ",
            "La pendiente teorica esperada de `log10(x)` vs `log10(W/RTT)` es **1.00**:\n\n",
            "| Modelo de eje X (`W / RTT`) | Pendiente ± IC 95% | R² | Error porcentual medio (MAPE) |\n",
            "|:---|---:|---:|---:|\n",
        ])
        for etq, col_x in [
            ("W pedida / RTT configurado", "pred_cfg_mbps"),
            ("W efectiva / RTT configurado", "pred_ef_cfg_mbps"),
            ("W efectiva / srtt medido", "pred_ef_srtt_mbps"),
        ]:
            r = regresion_loglog(reg_lim[col_x], reg_lim["goodput_mbps"])
            md.append(
                f"| {etq} | {r['pendiente']:.4f} ± {r['ic_pendiente']:.4f} | "
                f"{r['r2']:.4f} | {r['mape_pct']:.2f}% |\n"
            )

        algos_p = sorted(reg_lim["algo"].unique())
        grupos_algo = [reg_lim[reg_lim["algo"] == a]["goodput_mbps"].to_numpy() for a in algos_p]
        if len(grupos_algo) >= 2 and all(len(g) > 0 for g in grupos_algo):
            _, p_kw = stats.kruskal(*grupos_algo)
            md.append(
                f"\n- **Prueba de Kruskal-Wallis entre algoritmos en region limitada por ventana:** "
                f"`p = {p_kw:.4f}` (si `p > 0.05`, el algoritmo de congestion no altera el goodput cuando manda W).\n"
            )

    if not df_libre.empty:
        md.extend([
            "\n## 4. Ventana Libre con Buffer = BDP (`Reno` vs `Cubic` vs `BBR`)\n",
            "| RTT cfg (ms) | BDP (kB) | Algoritmo | Goodput ± IC (Mbit/s) | Llenado `x / (eta*C)` | "
            "srtt medio (ms) | Cola media TBF (kB) | Cola / BDP (%) | Retransm. |\n",
            "|---:|---:|:---|---:|---:|---:|---:|---:|---:|\n",
        ])
        for rtt_ms in sorted(df_libre["rtt_cfg_ms"].unique()):
            for algo in ["reno", "cubic", "bbr"]:
                sub = df_libre[(df_libre["rtt_cfg_ms"] == rtt_ms) & (df_libre["algo"] == algo)]
                if sub.empty:
                    continue
                mg, hg = ic_t(sub["goodput_mbps"])
                bdp_v = sub["bdp_kb"].mean()
                ut_v = sub["util_pct"].mean()
                sr_v = sub["srtt_ms"].mean()
                cm_v = sub["cola_prom_kb"].mean()
                cp_v = sub["cola_pct_bdp"].mean()
                rt_v = sub["retransmits"].mean()
                md.append(
                    f"| {rtt_ms:.0f} | {bdp_v:.1f} | {algo} | {mg:.2f} ± {hg:.2f} | "
                    f"{ut_v:.1f}% | {sr_v:.2f} | {cm_v:.1f} | {cp_v:.1f}% | {rt_v:.1f} |\n"
                )

    (DIR_BASE / "resumen.md").write_text("".join(md))
    print(f"[1/3] Resumen guardado en {DIR_BASE / 'resumen.md'}")

    # ---------------- GRAFICO 1: LEY x = min(eta*C, W/RTT) POR RTT + CRECIMIENTO DE COLA ----------------
    if not df_fija.empty:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.8, 5.7))
        rtts_unicos = sorted(df_fija["rtt_cfg_ms"].unique())
        colores_rtt = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
        marcadores_rtt = ["o", "s", "^", "D", "v"]

        for idx_r, rtt_v in enumerate(rtts_unicos):
            sub_r = df_fija[df_fija["rtt_cfg_ms"] == rtt_v]
            ax1.scatter(
                sub_r["pred_ef_srtt_mbps"],
                sub_r["goodput_mbps"],
                alpha=0.72,
                s=32,
                color=colores_rtt[idx_r % len(colores_rtt)],
                marker=marcadores_rtt[idx_r % len(marcadores_rtt)],
                label=f"RTT = {int(rtt_v)} ms",
            )

        eta_c = df_fija["eta_c_mbps"].iloc[0]
        x_grid = np.logspace(0.3, 2.95, 250)
        y_teo = np.minimum(eta_c, x_grid)
        ax1.plot(x_grid, y_teo, "k--", lw=1.8, label=f"Teo: min(eta*C={eta_c:.1f}, W/srtt)")

        ax1.set_xscale("log")
        ax1.set_yscale("log")
        ax1.set_xlabel("Tasa teorica por ventana efectiva: W_ef / srtt (Mbit/s)")
        ax1.set_ylabel("Goodput medido x (Mbit/s)")
        ax1.set_title("Ley x = min(eta*C, W/RTT) segun RTT Configurado")
        ax1.grid(True, which="both", ls="--", alpha=0.45)
        ax1.legend(fontsize=8.5, loc="lower right")

        df_cola_w = df_fija[df_fija["algo"].isin(["reno", "cubic"])].copy()
        if df_cola_w.empty:
            df_cola_w = df_fija.copy()

        for idx_r, rtt_v in enumerate(rtts_unicos):
            sub_r = df_cola_w[df_cola_w["rtt_cfg_ms"] == rtt_v]
            ax2.scatter(
                sub_r["w_sobre_bdp"],
                sub_r["cola_sobre_bdp"],
                alpha=0.75,
                s=34,
                color=colores_rtt[idx_r % len(colores_rtt)],
                marker=marcadores_rtt[idx_r % len(marcadores_rtt)],
                label=f"RTT = {int(rtt_v)} ms",
            )

        xmax_w = max(2.5, float(df_cola_w["w_sobre_bdp"].max()) * 1.05)
        w_norm_grid = np.linspace(0.0, xmax_w, 300)
        cola_teo_norm = np.maximum(0.0, w_norm_grid - 1.0)
        ax2.plot(
            w_norm_grid,
            cola_teo_norm,
            "k--",
            lw=1.8,
            label="Teo: max(0, W_ef / BDP - 1)",
        )
        ax2.axvline(1.0, color="#555555", ls=":", lw=1.5, label="Umbral W = 1 BDP")

        ax2.set_xlim(0, xmax_w)
        ax2.set_ylim(-0.25, max(1.5, float(df_cola_w["cola_sobre_bdp"].max()) * 1.12))
        ax2.set_xlabel("Ventana efectiva normalizada (W_ef / BDP)")
        ax2.set_ylabel("Ocupacion de cola normalizada (Cola / BDP)")
        ax2.set_title("Crecimiento de la Cola en r_bn cuando W Supera el BDP")
        ax2.grid(True, ls="--", alpha=0.5)
        ax2.legend(fontsize=8.5, loc="upper left")

        plt.tight_layout()
        g1 = DIR_BASE / "T2_1_Ventana_Fija_y_Cola.png"
        plt.savefig(g1, dpi=300)
        plt.close()
        print(f"[2/3] Grafico guardado en {g1}")

    # ---------------- GRAFICO 2: DINAMICA TEMPORAL (RTT=20ms) + OCUPACION POR RTT ----------------
    if series_temporales and not df_libre.empty:
        fig, (ax1, ax2) = plt.subplots(
            1, 2, figsize=(15.0, 5.6), gridspec_kw={"width_ratios": [1.35, 1.0]}
        )
        col_algo = {"reno": "#1f77b4", "cubic": "#ff7f0e", "bbr": "#2ca02c"}
        algos_ord = ["reno", "cubic", "bbr"]

        # Panel izquierdo: Dinamica temporal estacionaria en RTT = 20 ms
        rtt_ref = 20.0 if any(k[0] == 20.0 for k in series_temporales) else sorted({k[0] for k in series_temporales})[0]
        bdp_ref_kb = (CAPACIDAD_MBPS * 1e6 / 8.0) * (rtt_ref / 1000.0) / 1024.0

        t_lim = 17.4
        for algo in algos_ord:
            if (rtt_ref, algo) not in series_temporales:
                continue
            t_arr, c_norm = series_temporales[(rtt_ref, algo)]
            mask = t_arr <= t_lim
            ax1.plot(
                t_arr[mask],
                c_norm[mask],
                lw=1.7,
                alpha=0.92,
                color=col_algo[algo],
                label=algo.upper(),
            )

        ax1.axhline(1.0, color="#d62728", ls="--", lw=1.4, label="Buffer lleno (1.0 BDP)")
        ax1.set_ylim(-0.04, 1.26)
        ax1.set_xlim(0, t_lim)
        ax1.set_xlabel("Tiempo estacionario (s)")
        ax1.set_ylabel("Ocupacion normalizada del buffer (Cola / BDP)")
        ax1.set_title(
            f"Dinamica Temporal de la Cola (B = 1 BDP = {bdp_ref_kb:.0f} kB | RTT = {int(rtt_ref)} ms)"
        )
        ax1.grid(True, ls="--", alpha=0.5)
        ax1.legend(loc="upper center", ncol=4, fontsize=8.5, frameon=True)

        # Panel derecho: Ocupacion media (% BDP) y llenado del enlace por RTT
        rtts = sorted(df_libre["rtt_cfg_ms"].unique())
        x = np.arange(len(rtts))
        w = 0.25

        for idx_a, algo in enumerate(algos_ord):
            means, errs, utils = [], [], []
            for rtt in rtts:
                sub = df_libre[(df_libre["rtt_cfg_ms"] == rtt) & (df_libre["algo"] == algo)]
                m, h = ic_t(sub["cola_pct_bdp"])
                means.append(m)
                errs.append(0.0 if np.isnan(h) else h)
                utils.append(sub["util_pct"].mean())

            offset = (idx_a - 1.0) * w
            bars = ax2.bar(
                x + offset,
                means,
                width=w,
                yerr=errs,
                capsize=3,
                color=col_algo[algo],
                label=algo.upper(),
                zorder=3,
            )
            for b, u_pct in zip(bars, utils):
                h_val = b.get_height()
                if not np.isnan(h_val):
                    ax2.text(
                        b.get_x() + b.get_width() / 2.0,
                        h_val + 2.5,
                        f"{h_val:.0f}%\n({u_pct:.0f}% C)",
                        ha="center",
                        va="bottom",
                        fontsize=7.0,
                    )

        ax2.axhline(100.0, color="#d62728", ls=":", lw=1.4, label="100% BDP")
        ax2.set_xticks(x)
        ax2.set_xticklabels([f"RTT = {int(r)} ms" for r in rtts])
        ax2.set_ylim(0, 118)
        ax2.set_ylabel("Cola media (% del BDP)")
        ax2.set_title("Cola Media y Llenado de Enlace (% eta*C) con B = 1 BDP")
        ax2.grid(True, axis="y", ls="--", alpha=0.5)
        ax2.legend(fontsize=8.2, loc="upper right", ncol=2)

        plt.tight_layout()
        g2 = DIR_BASE / "T2_2_Dinamica_Cola_BDP.png"
        plt.savefig(g2, dpi=300)
        plt.close()
        print(f"[3/3] Grafico guardado en {g2}")


if __name__ == "__main__":
    main()
