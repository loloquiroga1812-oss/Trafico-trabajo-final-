import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

DIR_BASE = Path("results/H5")
POLITICAS = ["random", "roundrobin", "random2", "leastconn", "central"]
ETIQUETAS_POL = {
    "random": "random(1)\n[1 sorteo]",
    "roundrobin": "roundrobin",
    "random2": "random(2)\n[2 sorteos]",
    "leastconn": "leastconn",
    "central": "central\n(maxconn 1)",
}


def ic_t(x, conf=0.95):
    """Media y semiancho del IC t de Student entre repeticiones independientes."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    m = float(np.mean(x)) if n else float("nan")
    if n < 2:
        return m, float("nan")
    h = stats.t.ppf(0.5 + conf / 2.0, n - 1) * np.std(x, ddof=1) / np.sqrt(n)
    return m, float(h)


def pk_random_ms(lam, ed_s, es_s, c0_ms):
    # Cada servidor recibe Poisson(lam/K); servicio S = D + s0 (D ~ Exp(ed_s))
    rho = lam * es_s
    if rho >= 0.9999:
        return float("inf")
    cs2 = (ed_s / es_s) ** 2
    wq_s = (rho * es_s / (2.0 * (1.0 - rho))) * (1.0 + cs2)
    return (es_s + wq_s) * 1000.0 + c0_ms


def erlang_c_ms(lam, k, es_s, c0_ms):
    a = lam * es_s
    rho = a / float(k)
    if rho >= 0.9999:
        return float("inf")
    suma = sum((a ** n) / math.factorial(n) for n in range(k))
    tope = ((a ** k) / math.factorial(k)) / (1.0 - rho)
    p_wait = tope / (suma + tope)
    wq_s = (p_wait * es_s) / (k * (1.0 - rho))
    return (es_s + wq_s) * 1000.0 + c0_ms


def simular_politica_traza(a_s, s_s, k, politica, c0_ms=0.0, gap_s=0.0, semilla=123):
    n = len(a_s)
    t_ms = np.empty(n, dtype=float)
    fin_srv = [0.0] * k
    colas_fin = [[] for _ in range(k)]
    rng = np.random.default_rng(semilla)

    for idx in range(n):
        arr = float(a_s[idx])
        srv_s = float(s_s[idx])

        if politica == "central":
            elegido = min(range(k), key=lambda i: fin_srv[i])
            disp = fin_srv[elegido] + gap_s if fin_srv[elegido] > 0.0 else 0.0
            ini = arr if arr > disp else disp
            fin = ini + srv_s
            fin_srv[elegido] = fin
            t_ms[idx] = (fin - arr) * 1000.0 + c0_ms
            continue

        if politica == "random":
            elegido = int(rng.integers(0, k))
        elif politica == "roundrobin":
            elegido = idx % k
        elif politica == "random2":
            cands = rng.choice(k, size=2, replace=False)
            act0 = sum(1 for tf in colas_fin[cands[0]] if tf > arr)
            act1 = sum(1 for tf in colas_fin[cands[1]] if tf > arr)
            if act0 < act1:
                elegido = int(cands[0])
            elif act1 < act0:
                elegido = int(cands[1])
            else:
                elegido = int(cands[int(rng.integers(0, 2))])
        elif politica == "leastconn":
            activos = [
                sum(1 for tf in colas_fin[i] if tf > arr) for i in range(k)
            ]
            min_act = min(activos)
            empates = [i for i, v in enumerate(activos) if v == min_act]
            elegido = empates[idx % len(empates)]
        else:
            raise ValueError(f"Politica invalida: {politica}")

        ini = arr if arr > fin_srv[elegido] else fin_srv[elegido]
        fin = ini + srv_s
        fin_srv[elegido] = fin
        if politica in ("random2", "leastconn"):
            colas_fin[elegido] = [
                tf for tf in colas_fin[elegido] if tf > arr
            ] + [fin]
        t_ms[idx] = (fin - arr) * 1000.0 + c0_ms

    return t_ms


def simular_asignacion_real(a_s, s_s, k, seq_servidores, c0_ms=0.0):
    n = len(a_s)
    t_ms = np.empty(n, dtype=float)
    fin_srv = [0.0] * k
    for idx in range(n):
        arr = float(a_s[idx])
        srv_s = float(s_s[idx])
        elegido = int(seq_servidores[idx]) % k
        ini = arr if arr > fin_srv[elegido] else fin_srv[elegido]
        fin = ini + srv_s
        fin_srv[elegido] = fin
        t_ms[idx] = (fin - arr) * 1000.0 + c0_ms
    return t_ms


def validar_simuladores_sintetico():
    rng = np.random.default_rng(55555)
    n = 120000
    es = 0.010
    k = 4
    rho = 0.75
    lam = (k * rho) / es
    a_s = np.cumsum(rng.exponential(1.0 / lam, size=n))
    s_s = rng.exponential(es, size=n)

    t_pk = pk_random_ms(lam / k, es, es, 0.0)
    t_sim_rnd = float(
        np.mean(simular_politica_traza(a_s, s_s, k, "random", 0.0, 0.0, 99)[4000:])
    )
    err_rnd = ((t_sim_rnd - t_pk) / t_pk) * 100.0

    t_ec = erlang_c_ms(lam, k, es, 0.0)
    t_sim_cen = float(
        np.mean(simular_politica_traza(a_s, s_s, k, "central", 0.0, 0.0, 99)[4000:])
    )
    err_cen = ((t_sim_cen - t_ec) / t_ec) * 100.0

    return (t_pk, t_sim_rnd, err_rnd), (t_ec, t_sim_cen, err_cen)


def leer_traza_haproxy(d_cor, k_srv, n_ped):
    f_tr = d_cor / "haproxy_trace.csv"
    if not f_tr.exists():
        return None
    lineas = [ln.strip() for ln in f_tr.read_text().splitlines() if ln.strip()]
    if len(lineas) < n_ped:
        return None
    ult = lineas[-n_ped:]
    seq = []
    for ln in ult:
        sv = ln.split(",")[0].strip()
        if sv.startswith("api") and sv[3:].isdigit():
            seq.append(int(sv[3:]) - 1)
        else:
            return None
    return seq if len(seq) == n_ped else None


def etiquetar_barras(ax, barras, errores, ymin, ymax, con_fondo=False):
    rango = max(1.0, ymax - ymin)
    bbox_cfg = (
        dict(boxstyle="round,pad=0.14", fc="white", ec="none", alpha=0.88)
        if con_fondo
        else None
    )
    for bar, err in zip(barras, errores):
        h = bar.get_height()
        if np.isnan(h) or h < ymin or h > ymax:
            continue
        y_txt = min(ymax - rango * 0.06, h + err + rango * 0.018)
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            y_txt,
            f"{h:.1f}",
            ha="center",
            va="bottom",
            fontsize=7.8,
            bbox=bbox_cfg,
            zorder=5,
        )


def dibujar_panel_h5(fig, slot_gs, sub_e, k_srv, rho):
    x = np.arange(len(POLITICAS))
    w = 0.35

    mh = [ic_t(sub_e[sub_e["pol"] == p]["et_med_ms"]) for p in POLITICAS]
    med_v = [m for m, _ in mh]
    med_e = [0.0 if np.isnan(h) else h for _, h in mh]
    sim_v = [sub_e[sub_e["pol"] == p]["et_sim_gap_ms"].mean() for p in POLITICAS]

    pk_val = sub_e[sub_e["pol"] == "random"]["et_pk_ms"].mean()
    ec_val = sub_e[sub_e["pol"] == "central"]["et_ec_ms"].mean()

    max_med = max([v for v in med_v if not np.isnan(v)])
    otras = [v for v in (med_v[1:] + sim_v + [pk_val, ec_val]) if not np.isnan(v)]
    max_otras = max(otras) if otras else 100.0

    # Si random(1) supera mas de 2.5 veces el resto (ej. rho=0.9), usar eje partido
    if max_med > 2.5 * max_otras:
        ratio_top = 1.0
        ratio_bot = 3.2
        sub_gs = slot_gs.subgridspec(
            2, 1, height_ratios=[ratio_top, ratio_bot], hspace=0.09
        )
        ax_top = fig.add_subplot(sub_gs[0])
        ax_bot = fig.add_subplot(sub_gs[1], sharex=ax_top)

        ymax_low = max_otras * 1.28
        ymin_top = max(ymax_low * 1.5, max_med * 0.65)
        ymax_top = max(
            [v + e for v, e in zip(med_v, med_e) if not np.isnan(v)]
        ) * 1.14

        # Franja superior (solo se ven las barras altas, ej. random(1))
        b1_top = ax_top.bar(
            x - w / 2, med_v, width=w, yerr=med_e, capsize=3,
            color="#1f77b4", label="Medido (Real, IC 95%)", zorder=3
        )
        b2_top = ax_top.bar(
            x + w / 2, sim_v, width=w,
            color="#ff7f0e", label="Simulacion de traza", zorder=3
        )
        ax_top.set_ylim(ymin_top, ymax_top)
        ax_top.spines["bottom"].set_visible(False)
        ax_top.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
        ax_top.grid(True, axis="y", ls="--", alpha=0.5)
        ax_top.set_title(f"K = {k_srv} servidores | Carga rho = {rho}")

        etiquetar_barras(ax_top, b1_top, med_e, ymin_top, ymax_top, con_fondo=True)
        etiquetar_barras(ax_top, b2_top, [0.0] * len(sim_v), ymin_top, ymax_top)

        # Franja inferior (detalle de todas las demas politicas y lineas teoricas)
        med_e_bot = [
            e if (not np.isnan(v) and v <= ymax_low) else 0.0
            for v, e in zip(med_v, med_e)
        ]
        b1_bot = ax_bot.bar(
            x - w / 2, med_v, width=w, yerr=med_e_bot, capsize=3,
            color="#1f77b4", zorder=3
        )
        b2_bot = ax_bot.bar(
            x + w / 2, sim_v, width=w, color="#ff7f0e", zorder=3
        )

        # Lineas teoricas (se agregan a la leyenda combinada arriba)
        h_pk = None
        h_ec = None
        if not np.isnan(pk_val):
            h_pk = ax_bot.axhline(
                pk_val, color="#d62728", ls="--", lw=1.6, zorder=4,
                label=f"Teo P-K (random(1)): {pk_val:.1f} ms"
            )
        if not np.isnan(ec_val):
            h_ec = ax_bot.axhline(
                ec_val, color="#2ca02c", ls=":", lw=1.9, zorder=4,
                label=f"Teo Erlang C (central): {ec_val:.1f} ms"
            )

        ax_bot.set_ylim(0, ymax_low)
        ax_bot.spines["top"].set_visible(False)
        ax_bot.set_xticks(x)
        ax_bot.set_xticklabels([ETIQUETAS_POL[p] for p in POLITICAS], fontsize=8.5)
        ax_bot.set_ylabel("Tiempo medio de respuesta E[T] (ms)", y=0.66)
        ax_bot.grid(True, axis="y", ls="--", alpha=0.5)

        etiquetar_barras(ax_bot, b1_bot, med_e_bot, 0.0, ymax_low)
        etiquetar_barras(ax_bot, b2_bot, [0.0] * len(sim_v), 0.0, ymax_low)

        # Marcas diagonales de corte en ambos ejes Y
        d = 0.011
        kw_top = dict(transform=ax_top.transAxes, color="k", clip_on=False, lw=1.1)
        ax_top.plot((-d, +d), (-d * ratio_bot, +d * ratio_bot), **kw_top)
        ax_top.plot((1 - d, 1 + d), (-d * ratio_bot, +d * ratio_bot), **kw_top)

        kw_bot = dict(transform=ax_bot.transAxes, color="k", clip_on=False, lw=1.1)
        ax_bot.plot((-d, +d), (1 - d, 1 + d), **kw_bot)
        ax_bot.plot((1 - d, 1 + d), (1 - d, 1 + d), **kw_bot)

        handles = [h for h in (h_pk, h_ec, b1_top, b2_top) if h is not None]
        labels = [h.get_label() for h in handles]
        ax_top.legend(handles, labels, fontsize=7.6, loc="upper right")
        return

    # Caso normal (sin picos extremos, ej. rho = 0.6)
    ax = fig.add_subplot(slot_gs)
    b1 = ax.bar(
        x - w / 2, med_v, width=w, yerr=med_e, capsize=3,
        color="#1f77b4", label="Medido (Real, IC 95%)", zorder=3
    )
    b2 = ax.bar(
        x + w / 2, sim_v, width=w,
        color="#ff7f0e", label="Simulacion de traza", zorder=3
    )

    if not np.isnan(pk_val):
        ax.axhline(
            pk_val, color="#d62728", ls="--", lw=1.6, zorder=4,
            label=f"Teo P-K (random(1)): {pk_val:.1f} ms"
        )
    if not np.isnan(ec_val):
        ax.axhline(
            ec_val, color="#2ca02c", ls=":", lw=1.9, zorder=4,
            label=f"Teo Erlang C (central): {ec_val:.1f} ms"
        )

    tope = [v + e for v, e in zip(med_v, med_e)] + sim_v + [pk_val, ec_val]
    ymax = max([v for v in tope if not np.isnan(v)]) * 1.24
    ax.set_ylim(0, ymax)

    etiquetar_barras(ax, b1, med_e, 0.0, ymax)
    etiquetar_barras(ax, b2, [0.0] * len(sim_v), 0.0, ymax)

    ax.set_xticks(x)
    ax.set_xticklabels([ETIQUETAS_POL[p] for p in POLITICAS], fontsize=8.5)
    ax.set_title(f"K = {k_srv} servidores | Carga rho = {rho}")
    ax.set_ylabel("Tiempo medio de respuesta E[T] (ms)")
    ax.grid(True, axis="y", ls="--", alpha=0.5)
    ax.legend(fontsize=7.6, loc="upper right")


def main():
    f_cal = DIR_BASE / "calibracion_lb.json"
    if not DIR_BASE.exists() or not f_cal.exists():
        print("Faltan datos o calibracion_lb.json en results/H5.")
        return

    cal = json.loads(f_cal.read_text())
    s0_ms = float(cal["s0_ms"])
    s0_h_ms = float(cal.get("s0_handler_ms", s0_ms))
    s0_cg_cal_ms = float(cal.get("s0_cgroup_ms", s0_ms))
    c0_lb_ms = float(cal["c0_lb_ms"])
    gap_lb_api_ms = float(cal["gap_lb_api_ms"])

    val_rnd, val_cen = validar_simuladores_sintetico()

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
        k_srv = int(par["k_servidores"])
        pol = par["politica"]
        rho_obj = float(par["rho_objetivo"])
        rep = int(meta["repeticion"])

        a_s = df["t_envio_s"].to_numpy(dtype=float)
        d_s = df["cpu_pedida_ms"].to_numpy(dtype=float) / 1000.0
        s_s = d_s + (s0_ms / 1000.0)
        t_tot_arr = df["latencia_total_ms"].to_numpy(dtype=float)

        span_s = float(a_s[-1] - a_s[0])
        lam_med = (len(a_s) - 1) / span_s if span_s > 0 else 0.0
        es_med = float(np.mean(s_s))
        ed_med = float(np.mean(d_s))
        rho_med = (lam_med * es_med) / float(k_srv)

        t_pk_ms = pk_random_ms(lam_med / k_srv, ed_med, es_med, c0_lb_ms)
        t_ec_ms = erlang_c_ms(lam_med, k_srv, es_med, c0_lb_ms)

        sem_sim = int(meta.get("semilla", 1234)) + 17
        t_sim_arr = simular_politica_traza(
            a_s, s_s, k_srv, pol, c0_lb_ms, gap_s=0.0, semilla=sem_sim
        )
        t_sim_ms = float(np.mean(t_sim_arr))

        if pol == "central":
            t_sim_gap_arr = simular_politica_traza(
                a_s, s_s, k_srv, "central", c0_lb_ms,
                gap_s=gap_lb_api_ms / 1000.0, semilla=sem_sim
            )
            t_sim_gap_ms = float(np.mean(t_sim_gap_arr))
        else:
            t_sim_gap_ms = t_sim_ms

        rep_st = meta.get("reparto_haproxy_stats", {})
        conteos = [int(rep_st.get(f"api{i}", 0)) for i in range(1, k_srv + 1)]
        tot_st = sum(conteos)
        shares = [c / tot_st if tot_st > 0 else 1.0 / k_srv for c in conteos]
        desbalance_pct = (max(shares) - min(shares)) * 100.0 if shares else 0.0

        seq_real = leer_traza_haproxy(d, k_srv, len(df))
        if seq_real is not None:
            t_replay_ms = float(
                np.mean(simular_asignacion_real(a_s, s_s, k_srv, seq_real, c0_lb_ms))
            )
        else:
            t_replay_ms = float("nan")

        dur_real = float(meta.get("duracion_real_s", 130.0))
        cg = meta.get("cpu_cgroup_delta", {})
        usos = [
            cg.get(f"api{i}", {}).get("usage_usec", 0) for i in range(1, k_srv + 1)
        ]
        rhos_cg = [(u / 1e6) / dur_real for u in usos]
        rho_cg_prom = float(np.mean(rhos_cg)) if rhos_cg else 0.0
        rho_cg_max = float(np.max(rhos_cg)) if rhos_cg else 0.0

        if tot_st > 0:
            s0_cg_run_ms = (sum(usos) / 1000.0) / tot_st - ed_med * 1000.0
        else:
            s0_cg_run_ms = float("nan")

        filas.append({
            "k": k_srv,
            "rho_obj": rho_obj,
            "pol": pol,
            "rep": rep,
            "rho_med": rho_med,
            "rho_cg": rho_cg_prom,
            "rho_cg_max": rho_cg_max,
            "s0_cg_run_ms": s0_cg_run_ms,
            "et_med_ms": float(np.mean(t_tot_arr)),
            "et_sim_ms": t_sim_ms,
            "et_sim_gap_ms": t_sim_gap_ms,
            "et_replay_ms": t_replay_ms,
            "et_pk_ms": t_pk_ms,
            "et_ec_ms": t_ec_ms,
            "shares": shares,
            "desbalance_pct": desbalance_pct,
        })

    if not filas:
        print("Sin corridas validas en results/H5.")
        return

    df_res = pd.DataFrame(filas)

    def sel(k_srv, rho, pol=None):
        m = (df_res["k"] == k_srv) & (df_res["rho_obj"] == rho)
        if pol is not None:
            m = m & (df_res["pol"] == pol)
        return df_res[m]

    # ---------------- RESUMEN MARKDOWN ----------------
    md = [
        "# Resumen Estadistico - Experimento H5 (Balanceo con HAProxy)\n\n",
        "## 1. Calibracion Base y Validacion de Simuladores\n",
        f"- **Servicio vacio por servidor usado (s0):** `{s0_ms:.4f} ms` "
        f"(handler=`{s0_h_ms:.4f}`, cgroup=`{s0_cg_cal_ms:.4f}`)\n",
        f"- **Latencia externa vacia a traves de HAProxy (c0_lb):** `{c0_lb_ms:.4f} ms`\n",
        f"- **Salto ida/vuelta adicional HAProxy <-> API (gap_lb_api):** `{gap_lb_api_ms:.4f} ms`\n",
        f"- **Validacion sintetica `random` vs. P-K:** Teo=`{val_rnd[0]:.2f} ms`, ",
        f"Sim=`{val_rnd[1]:.2f} ms` (`{val_rnd[2]:+.2f}%`)\n",
        f"- **Validacion sintetica `central` vs. Erlang C:** Teo=`{val_cen[0]:.2f} ms`, ",
        f"Sim=`{val_cen[1]:.2f} ms` (`{val_cen[2]:+.2f}%`)\n\n",
        "## 2. Chequeo de Utilizacion (dos caminos)\n",
        "`rho_med` = lambda*E[S]/K desde los pedidos; `rho_cg` = CPU del cgroup / duracion. ",
        "Si difieren mucho, el s0 usado no representa el servicio real.\n\n",
        "| K | rho_obj | rho_med | rho_cg (prom) | rho_cg (max servidor) | s0 efectivo cgroup (ms) |\n",
        "|---:|---:|---:|---:|---:|---:|\n",
    ]
    for k_srv in [2, 4]:
        for rho in [0.6, 0.9]:
            sub = sel(k_srv, rho)
            if sub.empty:
                continue
            md.append(
                f"| {k_srv} | {rho:.1f} | {sub['rho_med'].mean():.3f} | "
                f"{sub['rho_cg'].mean():.3f} | {sub['rho_cg_max'].max():.3f} | "
                f"{sub['s0_cg_run_ms'].mean():.3f} |\n"
            )

    md.extend([
        "\n## 3. Comparacion de Politicas: Medido vs. Simulacion vs. P-K / Erlang C\n",
        "E[T] medido con IC 95% t de Student entre repeticiones. ",
        "`Sim traza` usa gap de red para `central`.\n\n",
        "| K | rho_obj | Politica | n | E[T] Medido ± IC (ms) | Sim traza (ms) | ",
        "Sim asig. real (ms) | Medido/Sim | Teo P-K / Erlang C (ms) |\n",
        "|---:|---:|:---|---:|---:|---:|---:|---:|---:|\n",
    ])
    for k_srv in [2, 4]:
        for rho in [0.6, 0.9]:
            for pol in POLITICAS:
                sub = sel(k_srv, rho, pol)
                if sub.empty:
                    continue
                t_m, h_m = ic_t(sub["et_med_ms"])
                t_s = sub["et_sim_gap_ms"].mean()
                t_rp = sub["et_replay_ms"].mean()
                rp_str = f"{t_rp:.2f}" if not np.isnan(t_rp) else "-"
                h_str = f"{h_m:.2f}" if not np.isnan(h_m) else "n/a"
                if pol == "random":
                    teo_str = f"{sub['et_pk_ms'].mean():.2f} (P-K)"
                elif pol == "central":
                    teo_str = f"{sub['et_ec_ms'].mean():.2f} (Erlang C)"
                else:
                    teo_str = "-"
                md.append(
                    f"| {k_srv} | {rho:.1f} | {pol} | {len(sub)} | "
                    f"{t_m:.2f} ± {h_str} | {t_s:.2f} | {rp_str} | "
                    f"{t_m / t_s:.2f} | {teo_str} |\n"
                )

    md.extend([
        "\n## 4. Reparto de Pedidos por Servidor y Efecto Ociosidad en `maxconn 1`\n",
        "| K | rho_obj | Politica | Reparto Medido (%) | Desbalance Max-Min (%) |\n",
        "|---:|---:|:---|:---|---:|\n",
    ])
    for k_srv in [2, 4]:
        for rho in [0.6, 0.9]:
            for pol in POLITICAS:
                sub = sel(k_srv, rho, pol)
                if sub.empty:
                    continue
                mat_sh = np.vstack(sub["shares"].to_numpy())
                prom_sh = np.mean(mat_sh, axis=0) * 100.0
                sh_txt = " / ".join(f"{v:.1f}%" for v in prom_sh)
                desb = sub["desbalance_pct"].mean()
                md.append(f"| {k_srv} | {rho:.1f} | {pol} | {sh_txt} | {desb:.2f}% |\n")

    md.extend([
        "\n### Impacto del tiempo ocioso entre pedidos en `central` (`maxconn 1`)\n",
        "| K | rho_obj | Erlang C Ideal (ms) | Sim `central` ideal (ms) | ",
        "Sim `central` + Gap Red (ms) | Medido `central` (ms) | Medido `leastconn` (ms) | ",
        "Penalizacion `maxconn 1` vs `leastconn` (ms) |\n",
        "|---:|---:|---:|---:|---:|---:|---:|---:|\n",
    ])
    for k_srv in [2, 4]:
        for rho in [0.6, 0.9]:
            sc = sel(k_srv, rho, "central")
            sl = sel(k_srv, rho, "leastconn")
            if sc.empty or sl.empty:
                continue
            ec_v = sc["et_ec_ms"].mean()
            si_v = sc["et_sim_ms"].mean()
            sg_v = sc["et_sim_gap_ms"].mean()
            mc_v = sc["et_med_ms"].mean()
            ml_v = sl["et_med_ms"].mean()
            md.append(
                f"| {k_srv} | {rho:.1f} | {ec_v:.2f} | {si_v:.2f} | {sg_v:.2f} | "
                f"{mc_v:.2f} | {ml_v:.2f} | {mc_v - ml_v:+.2f} |\n"
            )

    (DIR_BASE / "resumen.md").write_text("".join(md))
    print(f"[1/3] Resumen guardado en {DIR_BASE / 'resumen.md'}")

    # ---------------- GRAFICO 1 (con eje partido en rho=0.9) ----------------
    fig = plt.figure(figsize=(14.8, 9.6))
    gs = fig.add_gridspec(
        2, 2, hspace=0.28, wspace=0.18,
        left=0.06, right=0.98, top=0.94, bottom=0.08
    )
    escenarios = [
        ((0, 0), 2, 0.6),
        ((0, 1), 2, 0.9),
        ((1, 0), 4, 0.6),
        ((1, 1), 4, 0.9),
    ]
    for (r_idx, c_idx), k_srv, rho in escenarios:
        dibujar_panel_h5(fig, gs[r_idx, c_idx], sel(k_srv, rho), k_srv, rho)

    g1 = DIR_BASE / "H5_1_Politicas_y_Orden_Teorico.png"
    plt.savefig(g1, dpi=300)
    plt.close()
    print(f"[2/3] Grafico guardado en {g1}")

    # ---------------- GRAFICO 2 ----------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.5, 5.3))
    x = np.arange(len(POLITICAS))

    sub_k4 = sel(4, 0.9)
    if sub_k4.empty:
        sub_k4 = df_res[df_res["k"] == 4]

    col_srv = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
    bottoms = np.zeros(len(POLITICAS))
    for i in range(4):
        vals_srv = []
        for p in POLITICAS:
            sp = sub_k4[sub_k4["pol"] == p]
            if sp.empty:
                vals_srv.append(0.0)
            else:
                mat = np.vstack(sp["shares"].to_numpy())
                vals_srv.append(float(np.mean(mat[:, i])) * 100.0)
        vals_arr = np.array(vals_srv)
        ax1.bar(x, vals_arr, width=0.52, bottom=bottoms,
                color=col_srv[i], label=f"api{i + 1}")
        for j, v in enumerate(vals_arr):
            if v >= 8.0:
                ax1.text(x[j], bottoms[j] + v / 2.0, f"{v:.1f}%",
                         ha="center", va="center", color="white",
                         fontsize=8, fontweight="bold")
        bottoms += vals_arr

    ax1.axhline(25.0, color="k", ls="--", lw=1.2, alpha=0.7)
    ax1.set_xticks(x)
    ax1.set_xticklabels([ETIQUETAS_POL[p] for p in POLITICAS], fontsize=8.5)
    ax1.set_ylim(0, 108)
    ax1.set_title("Reparto de Pedidos por Servidor (K = 4, rho = 0.9)")
    ax1.set_ylabel("Porcentaje de pedidos recibidos (%)")
    ax1.legend(fontsize=8.5, ncol=4, loc="upper center")

    casos_c = [(2, 0.6), (2, 0.9), (4, 0.6), (4, 0.9)]
    etq_c = [f"K={k}\nrho={r}" for k, r in casos_c]
    xc = np.arange(len(casos_c))
    wc = 0.21

    def serie(pol, col):
        return [sel(k, r, pol)[col].mean() for k, r in casos_c]

    v_ec = serie("central", "et_ec_ms")
    v_sg = serie("central", "et_sim_gap_ms")
    v_mc = serie("central", "et_med_ms")
    v_ml = serie("leastconn", "et_med_ms")

    b_ec = ax2.bar(xc - 1.5 * wc, v_ec, width=wc, color="#2ca02c", label="Erlang C Ideal (0 gap)")
    b_sg = ax2.bar(xc - 0.5 * wc, v_sg, width=wc, color="#ff7f0e", label="Sim central + Gap Red")
    b_mc = ax2.bar(xc + 0.5 * wc, v_mc, width=wc, color="#d62728", label="Medido central (maxconn 1)")
    b_ml = ax2.bar(xc + 1.5 * wc, v_ml, width=wc, color="#1f77b4", label="Medido leastconn")

    for barras in (b_ec, b_sg, b_mc, b_ml):
        for bar in barras:
            h = bar.get_height()
            if not np.isnan(h):
                ax2.text(bar.get_x() + bar.get_width() / 2.0, h + 0.8, f"{h:.1f}",
                         ha="center", va="bottom", fontsize=7.3)

    ax2.set_xticks(xc)
    ax2.set_xticklabels(etq_c)
    ymax2 = max([v for v in (v_ec + v_sg + v_mc + v_ml) if not np.isnan(v)])
    ax2.set_ylim(0, ymax2 * 1.24)
    ax2.set_title("Impacto del Tiempo Ocioso en maxconn 1 (rho=0.6 vs. rho=0.9)")
    ax2.set_ylabel("Tiempo medio de respuesta E[T] (ms)")
    ax2.grid(True, axis="y", ls="--", alpha=0.5)
    ax2.legend(fontsize=7.8, loc="upper left")

    plt.tight_layout()
    g2 = DIR_BASE / "H5_2_Reparto_y_Ociosidad_Maxconn1.png"
    plt.savefig(g2, dpi=300)
    plt.close()
    print(f"[3/3] Grafico guardado en {g2}")


if __name__ == "__main__":
    main()
