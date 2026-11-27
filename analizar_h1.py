import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

DIR_BASE = Path("results/H1")


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


def cargar_serie(nombre_serie):
    dir_s = DIR_BASE / nombre_serie
    filas = []
    pools = {}
    if not dir_s.exists():
        return pd.DataFrame(), {}

    for d in sorted(dir_s.iterdir()):
        if not d.is_dir():
            continue
        f_meta = d / "meta.json"
        f_csv = d / "pedidos.csv"
        if not f_meta.exists() or not f_csv.exists():
            continue

        meta = json.loads(f_meta.read_text())
        df = pd.read_csv(f_csv)
        par = meta["parametros"]
        modo = par["modo"]
        cupo = par["cupo_cpu_pct"]
        d_ms = par["d_ms"]

        cpu_api = meta.get("cpu_cgroup_delta", {}).get("api1", {})
        n_ped = len(df)
        cg_ms = (cpu_api.get("usage_usec", 0) / 1000.0) / max(1, n_ped)
        thr_req = cpu_api.get("nr_throttled", 0) / max(1, n_ped)
        c0_serie = df["latencia_total_ms"] - df["t_handler_ms"]

        filas.append({
            "modo": modo,
            "cupo": cupo,
            "d_ms": d_ms,
            "rep": meta["repeticion"],
            "cpu_usada_ms": float(df["cpu_usada_ms"].mean()),
            "cpu_cgroup_ms": float(cg_ms),
            "handler_ms": float(df["t_handler_ms"].mean()),
            "lat_total_ms": float(df["latencia_total_ms"].mean()),
            "c0_ms": float(c0_serie.mean()),
            "thr_por_req": float(thr_req),
        })

        clave = (modo, cupo, d_ms)
        pools.setdefault(clave, []).append(df["t_handler_ms"].to_numpy())

    pools_cat = {k: np.concatenate(v) for k, v in pools.items()}
    return pd.DataFrame(filas), pools_cat


def main():
    df_base, _ = cargar_serie("base")
    df_cupo, pools_cupo = cargar_serie("cupo")

    if df_base.empty or df_cupo.empty:
        print("Faltan datos en results/H1/base o results/H1/cupo.")
        return

    demandas = sorted(df_base["d_ms"].unique())

    # --- ETAPA 1: s0, c0 y S = D + s0 en FIFO y PS ---
    s0_info = {}
    c0_info = {}
    for modo in ["FIFO", "PS"]:
        sub_d0 = df_base[(df_base["modo"] == modo) & (df_base["d_ms"] == 0)]
        mh, _, ich = ic_t_95(sub_d0["handler_ms"])
        mcg, _, iccg = ic_t_95(sub_d0["cpu_cgroup_ms"])
        s0_info[modo] = {
            "handler_m": mh, "handler_ic": ich,
            "cgroup_m": mcg, "cgroup_ic": iccg,
        }
        sub_m = df_base[df_base["modo"] == modo]
        mc0, _, icc0 = ic_t_95(sub_m["c0_ms"])
        c0_info[modo] = {"c0_m": mc0, "c0_ic": icc0}

    md = [
        "# Resumen Estadistico - Experimento H1\n\n",
        "## Etapa 1: Pedidos de a uno (D en {0,1,2,5,10,20} ms, FIFO y PS)\n\n",
        "### 1.1 Costo Pedido Vacio (s0) y Latencia Externa (c0)\n",
        "| Modo | s0 Handler +- IC95% (ms) | s0 cgroup +- IC95% (ms) | ",
        "c0 Red+Cliente +- IC95% (ms) |\n",
        "|:---|---:|---:|---:|\n",
    ]
    for modo in ["FIFO", "PS"]:
        si = s0_info[modo]
        ci = c0_info[modo]
        md.append(
            f"| {modo} | {si['handler_m']:.4f} +- {si['handler_ic']:.4f} | "
            f"{si['cgroup_m']:.4f} +- {si['cgroup_ic']:.4f} | "
            f"{ci['c0_m']:.4f} +- {ci['c0_ic']:.4f} |\n"
        )

    md.extend([
        "\n### 1.2 Verificacion CPU Consumida y S = D + s0\n",
        "| Modo | D (ms) | CPU Thread (ms) | CPU cgroup (ms) | ",
        "Handler Medido (ms) | S_teo = D + s0 (ms) | Error (%) |\n",
        "|:---|---:|---:|---:|---:|---:|---:|\n",
    ])
    for modo in ["FIFO", "PS"]:
        s0_h = s0_info[modo]["handler_m"]
        for d_ms in demandas:
            sub = df_base[
                (df_base["modo"] == modo) & (df_base["d_ms"] == d_ms)
            ]
            cpu_th = sub["cpu_usada_ms"].mean()
            cpu_cg = sub["cpu_cgroup_ms"].mean()
            h_med, _, h_ic = ic_t_95(sub["handler_ms"])
            s_teo = d_ms + s0_h
            err = ((h_med - s_teo) / s_teo) * 100.0 if s_teo > 0 else 0.0
            md.append(
                f"| {modo} | {d_ms} | {cpu_th:.3f} | {cpu_cg:.3f} | "
                f"{h_med:.3f} +- {h_ic:.3f} | {s_teo:.3f} | {err:+.2f}% |\n"
            )

    # --- ETAPA 2: Barrido de cupo de CPU (100%, 50%, 25%) ---
    md.extend([
        "\n## Etapa 2: Cupo de CPU (100%, 50% y 25%) con cada D\n",
        "| Cupo (%) | D (ms) | Handler +- IC95% (ms) | Teo S/cupo (ms) | ",
        "p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Throttles/req |\n",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n",
    ])
    cupos = sorted(df_cupo["cupo"].unique(), reverse=True)
    s0_fifo = s0_info["FIFO"]["handler_m"]
    for cupo in cupos:
        for d_ms in demandas:
            sub = df_cupo[
                (df_cupo["cupo"] == cupo) & (df_cupo["d_ms"] == d_ms)
            ]
            h_med, _, h_ic = ic_t_95(sub["handler_ms"])
            teo_c = (d_ms + s0_fifo) / (cupo / 100.0)
            arr = pools_cupo.get(("FIFO", cupo, d_ms), np.array([h_med]))
            p50, p95, p99 = np.percentile(arr, [50, 95, 99])
            mx = float(np.max(arr))
            thr = sub["thr_por_req"].mean()
            md.append(
                f"| {cupo}% | {d_ms} | {h_med:.3f} +- {h_ic:.3f} | "
                f"{teo_c:.2f} | {p50:.2f} | {p95:.2f} | {p99:.2f} | "
                f"{mx:.2f} | {thr:.3f} |\n"
            )

    (DIR_BASE / "resumen.md").write_text("".join(md))
    print(f"[1/4] Resumen guardado en {DIR_BASE / 'resumen.md'}")

    # Grafico 1 (Etapa 1): S = D + s0 y descomposicion con c0
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2))
    xs = np.array(demandas, dtype=float)
    ax1.plot(xs, xs, "k--", lw=1.5, label="Ideal CPU = D")

    estilos = {"FIFO": ("o-", "#1f77b4"), "PS": ("s--", "#ff7f0e")}
    for modo, (fmt, col) in estilos.items():
        ys_h, err_h = [], []
        for d_ms in demandas:
            sub = df_base[
                (df_base["modo"] == modo) & (df_base["d_ms"] == d_ms)
            ]
            mh, _, ich = ic_t_95(sub["handler_ms"])
            ys_h.append(mh)
            err_h.append(ich)
        ax1.errorbar(
            xs, ys_h, yerr=err_h, fmt=fmt, color=col,
            capsize=4, lw=2, label=f"Handler {modo} (S = D + s0)"
        )

    ax1.set_title("Etapa 1: Servicio Handler vs. D (FIFO y PS)")
    ax1.set_xlabel("Demanda de CPU pedida D (ms)")
    ax1.set_ylabel("Tiempo medido (ms)")
    ax1.set_xticks(xs)
    ax1.grid(True, ls="--", alpha=0.5)
    ax1.legend()

    ys_h_f, ys_c0_f = [], []
    for d_ms in demandas:
        sub = df_base[(df_base["modo"] == "FIFO") & (df_base["d_ms"] == d_ms)]
        ys_h_f.append(sub["handler_ms"].mean())
        ys_c0_f.append(sub["c0_ms"].mean())

    ax2.bar(
        xs, ys_h_f, width=1.1, color="#5b8db8",
        edgecolor="black", label="Handler servidor (S = D + s0)"
    )
    ax2.bar(
        xs, ys_c0_f, width=1.1, bottom=ys_h_f,
        color="#e07b53", edgecolor="black", label="Red + Cliente (c0)"
    )
    ax2.set_title("Etapa 1: Descomposicion Latencia Total (S + c0)")
    ax2.set_xlabel("Demanda de CPU pedida D (ms)")
    ax2.set_ylabel("Latencia total promedio (ms)")
    ax2.set_xticks(xs)
    ax2.grid(True, ls="--", alpha=0.5, axis="y")
    ax2.legend()

    plt.tight_layout()
    g1 = DIR_BASE / "H1_1_Caracterizacion_Base.png"
    plt.savefig(g1, dpi=300)
    plt.close()
    print(f"[2/4] Grafico guardado en {g1}")

    # Grafico 2 (Etapa 2): Tiempo del handler por cupo de CPU
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2))
    col_c = {100: "#1f77b4", 50: "#ff7f0e", 25: "#d62728"}
    mar_c = {100: "o", 50: "s", 25: "^"}

    base_100 = {
        d_ms: df_cupo[
            (df_cupo["cupo"] == 100) & (df_cupo["d_ms"] == d_ms)
        ]["handler_ms"].mean()
        for d_ms in demandas
    }

    for cupo in cupos:
        ys_m, ys_e, ys_teo, ys_fac = [], [], [], []
        for d_ms in demandas:
            sub = df_cupo[
                (df_cupo["cupo"] == cupo) & (df_cupo["d_ms"] == d_ms)
            ]
            mh, _, ich = ic_t_95(sub["handler_ms"])
            ys_m.append(mh)
            ys_e.append(ich)
            ys_teo.append((d_ms + s0_fifo) / (cupo / 100.0))
            ys_fac.append(mh / base_100[d_ms])

        ax1.errorbar(
            xs, ys_m, yerr=ys_e, fmt=mar_c[cupo] + "-",
            color=col_c[cupo], capsize=4, lw=2,
            label=f"Medido Cupo {cupo}%"
        )
        ax1.plot(
            xs, ys_teo, "--", color=col_c[cupo], lw=1.2, alpha=0.8,
            label=f"Teo (D+s0)/{cupo/100:.2f}"
        )
        ax2.plot(
            xs, ys_fac, mar_c[cupo] + "-", color=col_c[cupo],
            lw=2, label=f"Cupo {cupo}% (ideal={100/cupo:.1f}x)"
        )

    ax1.set_title("Etapa 2: Tiempo del Handler por Cupo de CPU")
    ax1.set_xlabel("Demanda de CPU pedida D (ms)")
    ax1.set_ylabel("Tiempo del handler (ms)")
    ax1.set_xticks(xs)
    ax1.grid(True, ls="--", alpha=0.5)
    ax1.legend(fontsize=8.5)

    ax2.set_title("Etapa 2: Factor de Estiramiento respecto a 100%")
    ax2.set_xlabel("Demanda de CPU pedida D (ms)")
    ax2.set_ylabel("T_handler(cupo) / T_handler(100%)")
    ax2.set_xticks(xs)
    ax2.grid(True, ls="--", alpha=0.5)
    ax2.legend()

    plt.tight_layout()
    g2 = DIR_BASE / "H1_2_Cupos_CPU.png"
    plt.savefig(g2, dpi=300)
    plt.close()
    print(f"[3/4] Grafico guardado en {g2}")

    # Grafico 3 (Etapa 2): Colas de distribucion CFS para las 6 demandas
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5))
    axes_flat = axes.flatten()

    for idx, d_ms in enumerate(demandas):
        ax = axes_flat[idx]
        for cupo in cupos:
            arr = pools_cupo.get(("FIFO", cupo, d_ms))
            if arr is None or len(arr) == 0:
                continue
            arr_s = np.sort(arr)
            ccdf = 1.0 - (np.arange(1, len(arr_s) + 1) / len(arr_s))
            ax.step(
                arr_s, ccdf, where="post", color=col_c[cupo],
                lw=2, label=f"Cupo {cupo}% (p99={np.percentile(arr, 99):.1f}ms)"
            )

        ax.set_yscale("log")
        ax.set_ylim(1e-3, 1.05)
        ax.set_title(f"Cola CFS (CCDF) - D = {d_ms} ms")
        ax.set_xlabel("Tiempo del handler (ms)")
        if idx % 3 == 0:
            ax.set_ylabel("P(T_handler > t) [escala log]")
        ax.grid(True, which="both", ls="--", alpha=0.4)
        ax.legend(fontsize=8, loc="upper right")

    fig.suptitle(
        "Etapa 2: Efecto del Planificador CFS (periodo 10 ms) en la Cola",
        fontweight="bold", fontsize=12
    )
    plt.tight_layout()
    g3 = DIR_BASE / "H1_3_Colas_CFS.png"
    plt.savefig(g3, dpi=300)
    plt.close()
    print(f"[4/4] Grafico guardado en {g3}")


if __name__ == "__main__":
    main()
