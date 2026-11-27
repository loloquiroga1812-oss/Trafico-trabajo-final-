#!/usr/bin/env python3
"""
T1 - Graficos de RTT y jitter (srv -> cli). Solo lee results/T1-RTT/.

Genera:
  T1_RTT_Linealidad.png    RTT medido vs configurado + residuos (3 paneles)
  T1_RTT_Distribucion.png  forma de la distribucion a RTT=50 ms (3 histogramas)

Requiere: numpy, scipy, matplotlib.
"""
import json
import math
import re
import zlib
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats
from scipy.special import ndtr

DIR_BASE = Path("results/T1-RTT")
N_DESCARTE = 10
RTT_JIT = 50
COLORES = {0: "#1f77b4", 2: "#ff7f0e", 10: "#2ca02c"}
MARCAS = {0: "o", 2: "s", 10: "^"}
DESPLAZ = {0: 0.0, 2: -3.0, 10: 3.0}


def ic_t_95(valores):
    arr = np.asarray(valores, dtype=float)
    n = len(arr)
    media = float(np.mean(arr))
    if n < 2:
        return media, 0.0, 0.0
    std = float(np.std(arr, ddof=1))
    margen = float(stats.t.ppf(0.975, n - 1)) * std / math.sqrt(n)
    return media, std, margen


def paso_ping(x):
    x = np.asarray(x)
    return np.where(x < 1, 0.001,
           np.where(x < 10, 0.01,
           np.where(x < 100, 0.1, 1.0)))


def desquantizar(x, rng):
    return x + rng.uniform(-0.5, 0.5, size=x.shape) * paso_ping(x)


def lilliefors_mc(x, nsim=2000, seed=2024):
    x = np.sort(np.asarray(x, dtype=float))
    n = len(x)
    i = np.arange(1, n + 1)

    def estad(sorted_rows):
        z = (sorted_rows - sorted_rows.mean(axis=-1, keepdims=True)) / \
            sorted_rows.std(axis=-1, ddof=1, keepdims=True)
        c = ndtr(z)
        d_mas = (i / n - c).max(axis=-1)
        d_menos = (c - (i - 1) / n).max(axis=-1)
        return np.maximum(d_mas, d_menos)

    d_obs = float(estad(x))
    rng = np.random.default_rng(seed)
    sims = np.sort(rng.standard_normal((nsim, n)), axis=1)
    d_sim = estad(sims)
    p = (1.0 + np.sum(d_sim >= d_obs)) / (nsim + 1.0)
    return d_obs, float(p)


def fp(p):
    return "nan" if (p is None or not np.isfinite(p)) else f"{p:.3g}"


def cargar():
    f_base = DIR_BASE / "base.json"
    if not f_base.exists():
        raise SystemExit("Falta base.json. Ejecuta correr_T1_rtt.py")
    base = json.loads(f_base.read_text())

    corridas = []
    for d in sorted(DIR_BASE.iterdir()):
        if not d.is_dir() or d.name == "base":
            continue
        f_meta, f_ping = d / "meta.json", d / "ping.txt"
        if not f_meta.exists() or not f_ping.exists():
            continue
        meta = json.loads(f_meta.read_text())
        txt = f_ping.read_text()

        pares = {}
        for s, t in re.findall(r"icmp_seq=(\d+) .*?time=([\d\.]+) ms", txt):
            pares.setdefault(int(s), float(t))
        if not pares:
            continue
        seqs = np.array(sorted(pares))
        x_all = np.array([pares[s] for s in seqs])
        mask = seqs > N_DESCARTE

        rng = np.random.default_rng(zlib.crc32(meta["corrida"].encode()))
        x_dq = desquantizar(x_all, rng)

        par = meta["parametros"]
        res = meta["ping_resumen"]
        corridas.append({
            "rtt": par["rtt_configurado_ms"],
            "jit": par["jitter_configurado_ms"],
            "rep": meta["repeticion"],
            "avg": res["avg_ms"],
            "mdev": res["mdev_ms"],
            "x_raw": x_all[mask],
            "x_dq": x_dq[mask],
        })
    if not corridas:
        raise SystemExit("Sin corridas validas.")
    return base, corridas


def resumen_punto(r, j, sub, rtt_base):
    avgs = np.array([c["avg"] for c in sub])
    media, _, ic = ic_t_95(avgs)
    teo = r + rtt_base
    sig_emp, _, sig_ic = ic_t_95([c["mdev"] for c in sub])
    d = {
        "r": r, "j": j, "teo": teo, "media": media, "ic95": ic,
        "avgs": avgs,
        "err_ms": media - teo,
        "sig_teo": j * math.sqrt(2.0),
        "sig_emp": sig_emp, "sig_ic": sig_ic,
        "pool_raw": np.concatenate([c["x_raw"] for c in sub]),
        "pool": np.concatenate([c["x_dq"] for c in sub]),
        "ks_d": np.nan, "ks_p": np.nan,
        "lil_d": np.nan, "lil_p": np.nan,
        "sw_p": np.nan,
    }
    if j > 0:
        ks = stats.kstest(d["pool"], "norm", args=(teo, d["sig_teo"]))
        d["ks_d"], d["ks_p"] = float(ks.statistic), float(ks.pvalue)
        d["lil_d"], d["lil_p"] = lilliefors_mc(d["pool"])
        d["sw_p"] = float(stats.shapiro(d["pool"]).pvalue)
    return d


def main():
    base, corridas = cargar()
    rtt_base = base["rtt_base_ms"]

    grupos = {}
    for c in corridas:
        grupos.setdefault((c["rtt"], c["jit"]), []).append(c)
    T = {k: resumen_punto(k[0], k[1], v, rtt_base)
         for k, v in sorted(grupos.items())}

    rtts_j0 = sorted(r for (r, j) in T if j == 0)
    rtts_net = [r for r in rtts_j0 if r > 0]
    jits = sorted(j for (r, j) in T if r == RTT_JIT)

    xs_reg = np.array([c["rtt"] for c in corridas
                       if c["jit"] == 0 and c["rtt"] > 0], float)
    ys_reg = np.array([c["avg"] for c in corridas
                       if c["jit"] == 0 and c["rtt"] > 0], float)
    reg = stats.linregress(xs_reg, ys_reg)
    t_crit = float(stats.t.ppf(0.975, len(xs_reg) - 2))
    sl_ic = t_crit * reg.stderr
    ic_ic = t_crit * reg.intercept_stderr
    offset_medio = float(np.mean([T[(r, 0)]["err_ms"] for r in rtts_net]))

    # ==================================================================
    # Grafico 1: Linealidad (3 paneles)
    # ==================================================================
    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(19, 5.8), gridspec_kw={"width_ratios": [1.2, 1, 0.8]})
    xs = np.array(rtts_j0, float)
    xf = np.linspace(0, max(xs) * 1.02, 100)

    ax1.plot(xf, xf + rtt_base, "k--", lw=1.6,
             label=f"Teorico: RTT_cfg + {rtt_base:.3f} ms")
    ax1.plot(xf, reg.intercept + reg.slope * xf, ":", color="#d62728", lw=2,
             label=(f"Regresion (con netem): {reg.slope:.4f}"
                    f"$\\pm${sl_ic:.4f} * RTT + {reg.intercept:.3f}"
                    f"$\\pm${ic_ic:.3f}"))
    ax1.errorbar(rtts_net, [T[(r, 0)]["media"] for r in rtts_net],
                 yerr=[T[(r, 0)]["ic95"] for r in rtts_net], fmt="o",
                 color=COLORES[0], capsize=4, lw=1.5, ms=7,
                 label="Medido J = 0 ms (IC95%)")
    if 0 in rtts_j0:
        ax1.plot([0], [T[(0, 0)]["media"]], "o", mfc="none",
                 color=COLORES[0], ms=9, label="RTT = 0 (sin netem = base)")
    for j in jits:
        if j == 0:
            continue
        d = T[(RTT_JIT, j)]
        ax1.plot([RTT_JIT + DESPLAZ[j] * 3], [d["media"]], MARCAS[j],
                 color=COLORES[j], ms=8,
                 label=f"Medido J = {j} ms @ {RTT_JIT} ms")
    ax1.set_title("RTT medido vs. RTT configurado")
    ax1.set_xlabel("RTT configurado (ms)")
    ax1.set_ylabel("RTT medido por ping (ms)")
    ax1.set_xticks(xs)
    ax1.grid(True, ls="--", alpha=0.5)
    ax1.legend(loc="upper left", fontsize=8.5)

    ax2.axhline(0, color="k", ls="--", lw=1.2, label="Error cero")
    ax2.axhline(offset_medio, color="#d62728", ls=":", lw=1.8,
                label=f"Offset medio con netem: {offset_medio:+.2f} ms")
    ax2.errorbar(rtts_net, [T[(r, 0)]["err_ms"] for r in rtts_net],
                 yerr=[T[(r, 0)]["ic95"] for r in rtts_net], fmt="o-",
                 color=COLORES[0], capsize=4, lw=2, ms=7,
                 label="J = 0 ms (media $\\pm$ IC95%)")
    for r in rtts_net:
        ax2.plot([r] * len(T[(r, 0)]["avgs"]),
                 T[(r, 0)]["avgs"] - T[(r, 0)]["teo"],
                 ".", color="k", ms=4, alpha=0.6)
    ax2.plot([], [], ".", color="k", label="Repeticiones")
    ax2.set_ylim(-0.1, max(0.8, offset_medio * 1.6))
    ax2.set_title("Desvio vs. RTT configurado (J = 0)")
    ax2.set_xlabel("RTT configurado (ms)")
    ax2.set_ylabel("RTT_med - (RTT_cfg + RTT_base) (ms)")
    ax2.set_xticks(rtts_net)
    ax2.grid(True, ls="--", alpha=0.5)
    ax2.legend(loc="lower right", fontsize=8.5)

    ax3.axhline(0, color="k", ls="--", lw=1.2, label="Error cero")
    ax3.axhline(offset_medio, color="#d62728", ls=":", lw=1.8,
                label=f"Offset medio (J=0): {offset_medio:+.2f} ms")
    for pos, j in enumerate(jits):
        d = T[(RTT_JIT, j)]
        res_rep = d["avgs"] - d["teo"]
        ax3.plot(pos + np.linspace(-0.12, 0.12, len(res_rep)), res_rep, ".",
                 color="k", ms=6, alpha=0.7, zorder=3)
        ax3.errorbar([pos + 0.25], [d["err_ms"]], yerr=[d["ic95"]],
                     fmt=MARCAS[j], color=COLORES[j], capsize=5, ms=9, lw=2,
                     label=f"J = {j} ms: {d['err_ms']:+.2f}"
                           f"$\\pm${d['ic95']:.2f} ms")
    ax3.plot([], [], ".", color="k", label="Repeticiones")
    ax3.set_xticks(range(len(jits)))
    ax3.set_xticklabels([f"{j}" for j in jits])
    ax3.set_xlim(-0.5, len(jits) - 0.4)
    ax3.set_xlabel("Jitter J por sentido (ms)")
    ax3.set_ylabel("RTT_med - (RTT_cfg + RTT_base) (ms)")
    ax3.set_title(f"Desvio vs. jitter (RTT = {RTT_JIT} ms)")
    ax3.grid(True, ls="--", alpha=0.5)
    ax3.legend(loc="best", fontsize=8)

    plt.tight_layout()
    plt.savefig(DIR_BASE / "T1_RTT_Linealidad.png", dpi=200)
    plt.close()
    print("[1/2] T1_RTT_Linealidad.png")

    # ==================================================================
    # Grafico 2: Distribucion a RTT = 50 ms (solo los 3 histogramas)
    # ==================================================================
    pasos = {0: 0.1, 2: 0.5, 10: 2.0}
    fig, axes = plt.subplots(1, len(jits), figsize=(5.6 * len(jits), 5.8),
                             squeeze=False)
    for k, j in enumerate(jits):
        d = T[(RTT_JIT, j)]
        raw = d["pool_raw"]
        mu_e = float(np.mean(raw))
        ax = axes[0][k]

        paso = pasos.get(j, 0.5)
        b0 = math.floor(raw.min() / paso) * paso - paso / 2
        b1 = math.ceil(raw.max() / paso) * paso + paso / 2
        bins = np.arange(b0, b1 + paso, paso)
        ax.hist(raw, bins=bins, density=True, alpha=0.65, color="#5b8db8",
                edgecolor="black", label=f"Empirico (n={len(raw)})")

        if j > 0:
            xmin = min(raw.min(), d["teo"] - 4 * d["sig_teo"])
            xmax = max(raw.max(), d["teo"] + 4 * d["sig_teo"])
            xp = np.linspace(xmin, xmax, 500)
            ax.plot(xp, stats.norm.pdf(xp, d["teo"], d["sig_teo"]), "r--",
                    lw=2.2,
                    label=(f"Normal teorica N({d['teo']:.2f}, "
                           f"{d['sig_teo']:.2f})"))
            ax.plot(xp, stats.norm.pdf(xp, mu_e, d["sig_teo"]), "g:", lw=2.4,
                    label=(f"Normal con $\\sigma$ teorica\n"
                           f"centrada en $\\mu_{{emp}}$={mu_e:.2f}"))
            t_est = (f"\nKS: D={d['ks_d']:.3f} (p={fp(d['ks_p'])}) | "
                     f"Lilliefors: D={d['lil_d']:.3f} "
                     f"(p={fp(d['lil_p'])})\nShapiro p={fp(d['sw_p'])}")
        else:
            ax.axvline(d["teo"], color="r", ls="--", lw=2,
                       label=f"Teorico: {d['teo']:.2f} ms")
            ax.axvline(mu_e, color="g", ls=":", lw=2.4,
                       label=f"Media medida: {mu_e:.2f} ms")
            t_est = ""
        ax.set_title(
            f"J = {j} ms\n$\\mu_{{emp}}$={mu_e:.2f} | "
            f"$\\mu_{{teo}}$={d['teo']:.2f}\n"
            f"$\\sigma_{{emp}}$={d['sig_emp']:.2f} | "
            f"$\\sigma_{{teo}}$=J$\\sqrt{{2}}$={d['sig_teo']:.2f}{t_est}",
            fontsize=9.5)
        ax.set_xlabel("RTT (ms)")
        if k == 0:
            ax.set_ylabel("Densidad")
        ax.grid(True, ls="--", alpha=0.5)
        ax.legend(fontsize=8, loc="upper right")

    fig.suptitle(f"Forma de la distribucion del RTT (RTT = {RTT_JIT} ms, "
                 "srv -> cli)", fontweight="bold")
    plt.tight_layout()
    plt.savefig(DIR_BASE / "T1_RTT_Distribucion.png", dpi=200)
    plt.close()
    print("[2/2] T1_RTT_Distribucion.png")


if __name__ == "__main__":
    main()
