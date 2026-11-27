#!/usr/bin/env python3
"""Análisis de la parte 1. Lee los datos crudos y genera resumen.md + gráficos. No corre nada.

Uso (en el núcleo del host, nunca durante una medición):
  taskset -c 0 python analizar.py t1                  # las tres de T1 (también: t2, t3, t4, t5)
  taskset -c 0 python analizar.py t1-capacidad        # una sola
  taskset -c 0 python analizar.py t1 --results results-prueba
Escribe <results>/<exp>/resumen.md y <results>/<exp>/*.png
"""
import argparse
import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from scipy.stats import beta
from statsmodels.stats.diagnostic import lilliefors

ETA = 1448 / 1514          # payload TCP / trama Ethernet (con timestamps TCP)
CONF = 0.95
RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
import modelos                       # modelos analíticos y de fluido (T3)


# ----------------------------------------------------------------------------- utilidades estadísticas
def ic_t(x, conf=CONF):
    """Media e IC t de Student (semi-ancho) entre repeticiones independientes."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float(x.mean()), float("nan")
    h = stats.t.ppf(1 - (1 - conf) / 2, n - 1) * x.std(ddof=1) / np.sqrt(n)
    return float(x.mean()), float(h)


def lag1(x):
    x = np.asarray(x, float)
    if len(x) < 3 or x.std() == 0:
        return float("nan")
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


def clopper_pearson(k, n, alpha=1 - CONF):
    lo = 0.0 if k == 0 else beta.ppf(alpha / 2, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(1 - alpha / 2, k + 1, n - k)
    return float(lo), float(hi)


def f(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:.{nd}f}"


def pval(p):
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return "—"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def tabla_md(filas, cols):
    """filas: lista de dicts con valores ya formateados; cols: lista de (clave, encabezado)."""
    if not filas:
        return "_(sin datos)_\n"
    out = ["| " + " | ".join(h for _, h in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in filas:
        out.append("| " + " | ".join(str(r.get(k, "")) for k, _ in cols) + " |")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- carga de datos
def corridas(res, exp):
    out = []
    for m in sorted((res / exp).glob("*/*/meta.json")):
        meta = json.loads(m.read_text())
        if meta.get("status") == "ok":
            out.append((m.parent, meta))
    return out


def rep_de(meta):
    return int(re.search(r"rep(\d+)", meta["corrida"]).group(1))


def goodput_intervalos(path):
    """Mbit/s por intervalo de 1 s, sin los intervalos omitidos (-O) y sin el primero que
    queda después (con -R llega inflado: incluye datos en vuelo)."""
    j = json.loads(Path(path).read_text())
    ints = []
    for i in j["intervals"]:
        om = i["sum"].get("omitted", False) or any(s.get("omitted") for s in i.get("streams", []))
        if not om:
            ints.append(i)
    ints = ints[1:]
    return np.array([i["sum"]["bits_per_second"] for i in ints]) / 1e6


def bytes_por_trama(path):
    t = Path(path).read_text()
    m = re.search(r"Sent (\d+) bytes (\d+) pkt", t)
    if not m or int(m.group(2)) == 0:
        return float("nan")
    return int(m.group(1)) / int(m.group(2))


def leer_sonda(path):
    d = pd.read_csv(path)
    rec = d[d.t_recv_ns >= 0]
    rtt = ((rec.t_recv_ns - rec.t_send_ns) / 1e6).to_numpy()
    perdido = (d.t_recv_ns < 0).to_numpy()
    return rtt, perdido


def seccion_cpu(corr):
    """Frecuencia de los CPUs medidos (durante la medición) y uso de CPU de cada contenedor."""
    por_cpu, uso, govs, turbos = {}, {}, set(), set()
    viejo = []
    for d, meta in corr:
        for cpu, r in meta.get("freq_khz_medicion", {}).items():
            por_cpu.setdefault(cpu, []).append(r)
        viejo += list(meta.get("freq_khz_antes", {}).values())
        if meta.get("governor") is not None:
            govs.add(meta["governor"])
        if meta.get("no_turbo") is not None:
            turbos.add(meta["no_turbo"])
        dur = meta.get("duracion_medicion_s") or 0
        for c, dl in meta.get("cpu_cgroup", {}).get("delta", {}).items():
            if dur > 0 and "usage_usec" in dl:
                uso.setdefault(c, []).append(100 * dl["usage_usec"] / 1e6 / dur)
    txt = "### Chequeo de CPU\n\n"
    if govs or turbos:
        txt += f"Governor del host: {', '.join(sorted(govs)) or 'desconocido'}; no_turbo: {sorted(turbos)}. "
        if 0 in turbos or any(g != "performance" for g in govs):
            txt += ("**AVISO:** hay corridas con turbo activo o governor distinto de `performance`: "
                    "volver a correr `sudo sh scripts/host_setup.sh` antes de medir.")
        txt += "\n\n"
    if por_cpu:
        filas = [dict(cpu=c, mn=f(min(r["min"] for r in v) / 1e3, 0), me=f(np.mean([r["media"] for r in v]) / 1e3, 0),
                      mx=f(max(r["max"] for r in v) / 1e3, 0)) for c, v in sorted(por_cpu.items(), key=lambda t: int(t[0]))]
        txt += "Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:\n\n"
        txt += tabla_md(filas, [("cpu", "CPU"), ("mn", "Mín"), ("me", "Media"), ("mx", "Máx")])
        txt += "\n"
    elif viejo:
        txt += (f"Frecuencia de todos los CPUs al inicio de las corridas (MHz): mín {min(viejo)/1e3:.0f}, "
                f"media {np.mean(viejo)/1e3:.0f}, máx {max(viejo)/1e3:.0f}. (Foto previa, no confiable.)\n\n")
    if uso:
        filas = [dict(c=c, media=f(np.mean(v), 1), maxi=f(np.max(v), 1)) for c, v in sorted(uso.items())]
        txt += "Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):\n\n"
        txt += tabla_md(filas, [("c", "Contenedor"), ("media", "Media %"), ("maxi", "Máx %")])
    else:
        txt += "_No se pudo leer cpu.stat de los cgroups._\n"
    return txt


# ----------------------------------------------------------------------------- T1: capacidad
def an_capacidad(res):
    exp = "t1-capacidad"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas = []
    for d, meta in corr:
        p = meta["params"]
        x = goodput_intervalos(d / "iperf.json")
        filas.append(dict(algo=p["algo"], cap=p["C"], rep=rep_de(meta), gp=x.mean(), lag1=lag1(x),
                          bpt=bytes_por_trama(d / "tc_bn.txt")))
    df = pd.DataFrame(filas)
    df["eff"] = df.gp / (ETA * df.cap)

    resumen = []
    for (algo, cap), g in df.groupby(["algo", "cap"]):
        m, h = ic_t(g.gp)
        teo = ETA * cap
        t_p = stats.ttest_1samp(g.gp, teo).pvalue if len(g) >= 2 else float("nan")
        resumen.append(dict(algo=algo, cap=cap, n=len(g), m=m, h=h, teo=teo, err=100 * (m - teo) / teo,
                            p=t_p, lag1=g.lag1.mean(), bpt=g.bpt.mean()))
    r = pd.DataFrame(resumen).sort_values(["algo", "cap"])

    md = ["# T1 — Capacidad: goodput vs η·C\n"]
    md.append(f"η = 1448/1514 = {ETA:.4f}. Una trama Ethernet de 1514 B = 14 (Ethernet) + 20 (IP) + 20 (TCP) "
              f"+ 12 (opción timestamps TCP) + **1448 B de payload**. El tbf cuenta la trama completa (1514 B) "
              f"y iperf3 mide solo el payload, por eso el goodput máximo es η·C y no C.\n")
    md.append("Cada fila: media de las repeticiones (cada repetición = media de sus intervalos de 1 s, "
              "descartando el arranque y el primer intervalo) con IC 95 % t de Student entre repeticiones. "
              "El valor p es de una prueba t de una muestra contra η·C; el error en % es lo que importa en la práctica.\n")
    filas_md = [dict(algo=x.algo, cap=int(x.cap), n=int(x.n), gp=f(x.m, 2), ic=f(x.h, 2), teo=f(x.teo, 2),
                     err=f(x.err, 2), p=pval(x.p), bpt=f(x.bpt, 1), lag=f(x.lag1, 2)) for x in r.itertuples()]
    md.append(tabla_md(filas_md, [("algo", "Algoritmo"), ("cap", "C (Mbit/s)"), ("n", "n"),
                                   ("gp", "Goodput (Mbit/s)"), ("ic", "± IC95"), ("teo", "η·C"),
                                   ("err", "Error %"), ("p", "p (t)"), ("bpt", "B/trama tbf"),
                                   ("lag", "autocorr lag-1")]))
    md.append("**Bytes por trama del tbf** (columna B/trama): debe ser ≈1514; si es mucho mayor, quedaron "
              "offloads (GRO/GSO/TSO) agregando paquetes.\n")
    md.append(f"**Independencia de los intervalos de iperf3:** autocorrelación lag-1 media = "
              f"{f(df.lag1.mean(), 2)} (rango {f(df.lag1.min(), 2)} a {f(df.lag1.max(), 2)}). "
              "Por eso los IC se calculan entre repeticiones y no con los intervalos de una corrida.\n")

    md.append("## ANOVA de dos factores (eficiencia = goodput / (η·C))\n")
    ncel = df.groupby(["algo", "cap"]).ngroups
    if len(df) > ncel and df.groupby(["algo", "cap"]).size().min() >= 2 and df.algo.nunique() > 1:
        try:
            modelo = smf.ols("eff ~ C(algo) * C(cap)", data=df).fit()
            an = sm.stats.anova_lm(modelo, typ=2)
            filas_a = [dict(fuente=i, ss=f(row["sum_sq"], 6), gl=int(row["df"]),
                            F=f(row["F"], 2), p=pval(row["PR(>F)"])) for i, row in an.iterrows()]
            md.append(tabla_md(filas_a, [("fuente", "Fuente"), ("ss", "Suma cuad."), ("gl", "gl"),
                                          ("F", "F"), ("p", "p")]))
            sw = stats.shapiro(modelo.resid)
            lev = stats.levene(*[g.eff.values for _, g in df.groupby(["algo", "cap"])])
            md.append(f"Supuestos: Shapiro-Wilk de los residuos p = {pval(sw.pvalue)}; "
                      f"Levene (homocedasticidad entre celdas) p = {pval(lev.pvalue)}. "
                      "Si alguno es < 0.05, el ANOVA no es confiable y se usa Kruskal-Wallis.\n")
            kw = stats.kruskal(*[g.eff.values for _, g in df.groupby("algo")])
            ng = int(df.groupby("algo").size().min())
            md.append(f"Kruskal-Wallis de la eficiencia entre algoritmos: H = {f(kw.statistic, 2)}, "
                      f"p = {pval(kw.pvalue)}. Cada algoritmo reúne {ng} observaciones (todas las capacidades y "
                      f"repeticiones), así que no rige el límite p ≥ 0.027 que sí aplica al comparar grupos de 3.\n")
        except Exception as e:      # pragma: no cover
            md.append(f"_No se pudo calcular el ANOVA: {e}_\n")
    else:
        md.append("_Hacen falta ≥ 2 repeticiones por celda y ≥ 2 algoritmos para el ANOVA._\n")

    md.append(seccion_cpu(corr))

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    marcas = {"reno": "o", "cubic": "s", "bbr": "^"}
    caps = sorted(r.cap.unique())
    ax[0].plot(caps, [ETA * c for c in caps], "k--", label="η·C (teórico)")
    for algo, g in r.groupby("algo"):
        ax[0].errorbar(g.cap, g.m, yerr=g.h.fillna(0), marker=marcas.get(algo, "o"), capsize=3, label=algo)
    ax[0].set(xlabel="C configurada (Mbit/s)", ylabel="Goodput (Mbit/s)", title="Goodput medido vs η·C")
    ax[0].legend()
    ax[0].grid(alpha=.3)
    for algo, g in r.groupby("algo"):
        ax[1].errorbar(g.cap, 100 * g.m / g.teo, yerr=100 * g.h.fillna(0) / g.teo,
                       marker=marcas.get(algo, "o"), capsize=3, label=algo)
    ax[1].axhline(100, color="k", ls="--")
    ax[1].set(xlabel="C configurada (Mbit/s)", ylabel="% de η·C", title="Eficiencia respecto de η·C")
    ax[1].grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(res / exp / "capacidad.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T1: RTT y jitter
def an_rtt(res):
    exp = "t1-rtt"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas, pool = [], {}
    for d, meta in corr:
        x, perd = leer_sonda(d / "sonda.csv")
        p = meta["params"]
        filas.append(dict(serie=meta["serie"], rtt=p["rtt"], jit=p["jitter"], rep=rep_de(meta), n=len(x),
                          media=x.mean(), sd=x.std(ddof=1), lag1=lag1(x), perd=perd.mean()))
        pool.setdefault((meta["serie"], p["rtt"], p["jitter"]), []).append(x)
    df = pd.DataFrame(filas)

    md = ["# T1 — RTT y jitter\n"]
    md.append("Sonda UDP de eco (1 paquete cada 20 ms, resolución de µs; `ping` redondea a 3 cifras). "
              "Cada repetición aporta una media y un desvío; los IC son t de Student entre repeticiones.\n")

    base = df[df.serie == "base"]
    if len(base):
        base_m, base_h = ic_t(base.media)
        base_sd = base.sd.mean()
    else:
        base_m, base_h, base_sd = 0.0, float("nan"), 0.0
    md.append("## RTT base (sin netem ni tbf)\n")
    md.append(f"RTT base = **{f(base_m, 3)} ms** (IC95 ± {f(base_h, 3)}; desvío dentro de corrida {f(base_sd, 3)} ms). "
              "Incluye el costo de la sonda en Python y de dos saltos de router.\n")

    md.append("## RTT configurado vs medido (jitter 0)\n")
    sub = df[df.serie == "rtt"]
    filas_r, curva = [], []
    for rtt, g in sub.groupby("rtt"):
        m, h = ic_t(g.media)
        esp = rtt + base_m
        t_p = stats.ttest_1samp(g.media, esp).pvalue if len(g) >= 2 else float("nan")
        filas_r.append(dict(rtt=rtt, n=len(g), m=f(m, 3), h=f(h, 3), esp=f(esp, 3), d=f(m - esp, 3),
                            dp=f(100 * (m - esp) / esp, 2), p=pval(t_p), sd=f(g.sd.mean(), 3)))
        curva.append((rtt, m, h))
    md.append(tabla_md(filas_r, [("rtt", "RTT config (ms)"), ("n", "n"), ("m", "Medido (ms)"), ("h", "± IC95"),
                                  ("esp", "Esperado = config + base"), ("d", "Desvío (ms)"), ("dp", "Desvío %"),
                                  ("p", "p (t)"), ("sd", "Desvío dentro de corrida (ms)")]))

    md.append("## Jitter (RTT config = 50 ms)\n")
    md.append("netem suma una normal de desvío J **en cada sentido**; las dos son independientes, así que el RTT "
              "tendría desvío √2·J (sumando, de forma independiente, el ruido de la sonda). "
              "KS con parámetros fijados por la configuración (válido); Lilliefors estima media y desvío de la muestra.\n")
    sj = df[df.serie == "jitter"]
    filas_j, jit_datos = [], []
    for jit, g in sj.groupby("jit"):
        x = np.concatenate(pool[("jitter", 50, jit)])
        m, h = ic_t(g.media)
        s, hs = ic_t(g.sd)
        esp_s = float(np.sqrt(2 * jit ** 2 + base_sd ** 2))
        fila = dict(j=jit, m=f(m, 3), h=f(h, 3), esp_m=f(50 + base_m, 3), s=f(s, 3), hs=f(hs, 3), esp_s=f(esp_s, 3),
                    lag=f(g.lag1.mean(), 2), ks="—", lil="—", asim=f(stats.skew(x), 2), curt=f(stats.kurtosis(x), 2))
        if jit > 0:
            fila["ks"] = pval(stats.kstest(x, stats.norm(50 + base_m, esp_s).cdf).pvalue)
            fila["lil"] = pval(lilliefors(x, dist="norm")[1])
            jit_datos.append((jit, x, 50 + base_m, esp_s))
        filas_j.append(fila)
    md.append(tabla_md(filas_j, [("j", "J (ms)"), ("m", "RTT medio"), ("h", "± IC95"), ("esp_m", "Esperado"),
                                  ("s", "Desvío medido"), ("hs", "± IC95"), ("esp_s", "√(2J²+σ_base²)"),
                                  ("lag", "autocorr lag-1"), ("ks", "p KS"), ("lil", "p Lilliefors"),
                                  ("asim", "Asimetría"), ("curt", "Curtosis exc.")]))
    md.append("Con ~10³–10⁴ muestras, KS detecta desvíos minúsculos: mirar los gráficos (histograma y QQ) "
              "y no solo el p. Una autocorrelación lag-1 ≈ 0 justifica tratar los RTT de una corrida como independientes.\n")

    md.append(seccion_cpu(corr))

    fig, ax = plt.subplots(figsize=(5.5, 4.2))
    if curva:
        cx, cm, ch = zip(*curva)
        ax.errorbar(cx, cm, yerr=np.nan_to_num(ch), fmt="o", capsize=3, label="medido")
        xs = np.linspace(0, max(cx), 50)
        ax.plot(xs, xs + base_m, "k--", label="config + RTT base")
    ax.set(xlabel="RTT configurado (ms)", ylabel="RTT medido (ms)", title="RTT medido vs configurado")
    ax.legend()
    ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(res / exp / "rtt.png", dpi=130)
    plt.close(fig)

    if jit_datos:
        fig, ax = plt.subplots(2, len(jit_datos), figsize=(5 * len(jit_datos), 7), squeeze=False)
        for k, (jit, x, mu, sg) in enumerate(jit_datos):
            ax[0][k].hist(x, bins=50, density=True, alpha=.6, label="medido")
            xs = np.linspace(x.min(), x.max(), 200)
            ax[0][k].plot(xs, stats.norm.pdf(xs, mu, sg), "r", label=f"N({mu:.1f}, {sg:.2f}²)")
            ax[0][k].set(title=f"RTT con J = {jit} ms", xlabel="RTT (ms)")
            ax[0][k].legend()
            stats.probplot(x, dist="norm", plot=ax[1][k])
            ax[1][k].set_title("QQ-plot contra normal")
        fig.tight_layout()
        fig.savefig(res / exp / "jitter.png", dpi=130)
        plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T1: pérdida
def an_perdida(res):
    exp = "t1-perdida"
    corr = corridas(res, exp)
    if not corr:
        return None
    rng = np.random.default_rng(12345)
    filas, porp = [], {}
    for d, meta in corr:
        _, perd = leer_sonda(d / "sonda.csv")
        p = meta["params"]["loss"]
        n, k = len(perd), int(perd.sum())
        porp.setdefault(p, []).append((rep_de(meta), n, k, perd))
    md = ["# T1 — Pérdida\n"]
    md.append("Sonda UDP a 1000 paquetes/s. La pérdida de netem actúa solo en el sentido de los datos, o sea "
              "sobre el eco. Cada paquete es un ensayo de Bernoulli: se estima la proporción con el intervalo "
              "exacto de Clopper-Pearson (no Wald, que falla con pocas pérdidas).\n")

    md.append("## Proporción medida\n")
    filas_p = []
    for p in sorted(porp):
        runs = sorted(porp[p], key=lambda t: t[0])
        N = sum(r[1] for r in runs)
        K = sum(r[2] for r in runs)
        lo, hi = clopper_pearson(K, N)
        dentro = "sí" if lo <= p / 100 <= hi else "no"
        if p == 0:
            dentro = "sí" if K == 0 else "no"
        chi = "—"
        if p > 0 and len(runs) >= 2:
            tabla = np.array([[r[2], r[1] - r[2]] for r in runs])
            chi = pval(stats.chi2_contingency(tabla)[1])
        por_rep = ", ".join(f(100 * r[2] / r[1], 3) for r in runs)
        filas_p.append(dict(p=p, N=N, K=K, ph=f(100 * K / N, 4), lo=f(100 * lo, 4), hi=f(100 * hi, 4),
                            dentro=dentro, reps=por_rep, chi=chi))
    md.append(tabla_md(filas_p, [("p", "p config (%)"), ("N", "Paquetes"), ("K", "Perdidos"),
                                  ("ph", "p̂ (%)"), ("lo", "CP inf (%)"), ("hi", "CP sup (%)"),
                                  ("dentro", "p config dentro del IC"), ("reps", "p̂ por repetición (%)"),
                                  ("chi", "p χ² homogeneidad")]))
    md.append("χ² de homogeneidad: ¿la proporción es la misma entre repeticiones? (p grande = sí).\n")

    md.append("## ¿Independientes o en ráfagas?\n")
    md.append("Dos pruebas por corrida. (1) **Índice de dispersión**: se cuentan pérdidas en bloques de 100 "
              "paquetes consecutivos; con pérdidas independientes Var/media = 1 − p. El valor p sale de simular "
              "10⁴ veces ese mismo experimento con pérdidas independientes (bootstrap paramétrico): no se usa la "
              "aproximación normal, que falla con p chico. (2) **P(pérdida dado que la anterior se perdió)**, con IC de "
              "Clopper-Pearson: si es ≈ p, no hay ráfagas.\n")
    filas_b = []
    m = 100
    for p in sorted(porp):
        if p == 0:
            continue
        for rep, n, k, perd in sorted(porp[p], key=lambda t: t[0]):
            B = n // m
            cuentas = perd[:B * m].reshape(B, m).sum(axis=1)
            ph = perd.mean()
            idx = cuentas.var(ddof=1) / cuentas.mean() if cuentas.mean() > 0 else float("nan")
            sim = rng.binomial(m, ph, size=(10000, B))
            sim_idx = sim.var(axis=1, ddof=1) / np.where(sim.mean(axis=1) > 0, sim.mean(axis=1), np.nan)
            sim_idx = sim_idx[~np.isnan(sim_idx)]
            p_mc = float(2 * min((sim_idx >= idx).mean(), (sim_idx <= idx).mean())) if not np.isnan(idx) else float("nan")
            p_mc = min(p_mc, 1.0)
            prev = perd[:-1]
            k_dos = int((perd[1:] & prev).sum())
            n_prev = int(prev.sum())
            cond = k_dos / n_prev if n_prev else float("nan")
            lo, hi = clopper_pearson(k_dos, n_prev) if n_prev else (float("nan"), float("nan"))
            filas_b.append(dict(p=p, rep=rep, idx=f(idx, 3), teo=f(1 - ph, 3), pmc=pval(p_mc),
                                cond=f(100 * cond, 2), lo=f(100 * lo, 2), hi=f(100 * hi, 2), ph=f(100 * ph, 3)))
    md.append(tabla_md(filas_b, [("p", "p config (%)"), ("rep", "Rep"), ("idx", "Índice Var/media"),
                                  ("teo", "Esperado 1 − p̂"), ("pmc", "p (simulación)"),
                                  ("cond", "P(pérdida tras pérdida) %"), ("lo", "CP inf"), ("hi", "CP sup"),
                                  ("ph", "p̂ %")]))
    md.append("Con muchas corridas, algún p < 0.05 por azar es esperable (~5 % de las pruebas); "
              "lo que indica ráfagas es un patrón sistemático (índice > 1 y P(pérdida tras pérdida) claramente > p̂ en todas las repeticiones).\n")

    md.append(seccion_cpu(corr))

    fig, ax = plt.subplots(figsize=(5.5, 4.2))
    ps = [x for x in sorted(porp) if x > 0]
    est = []
    for p in ps:
        N = sum(r[1] for r in porp[p])
        K = sum(r[2] for r in porp[p])
        lo, hi = clopper_pearson(K, N)
        est.append((100 * K / N, 100 * lo, 100 * hi))
    if ps:
        e = np.array(est)
        ax.errorbar(ps, e[:, 0], yerr=[e[:, 0] - e[:, 1], e[:, 2] - e[:, 0]], fmt="o", capsize=3,
                    label="medido (IC Clopper-Pearson)")
        ax.plot([min(ps), max(ps)], [min(ps), max(ps)], "k--", label="y = x")
    ax.set(xscale="log", yscale="log", xlabel="p configurada (%)", ylabel="p medida (%)",
           title="Pérdida medida vs configurada")
    ax.legend()
    ax.grid(alpha=.3, which="both")
    fig.tight_layout()
    fig.savefig(res / exp / "perdida.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T2: lectura de ss y de la cola
def parse_ss(texto):
    """Extrae del texto de `ss -tinm` los campos del socket de DATOS (el de más bytes_acked)."""
    mejor = None
    # Cada conexión empieza en una línea sin sangría (con o sin columna de estado: `ss ... state established`
    # la omite) y sus detalles van en líneas con sangría. El encabezado no trae campos y se descarta.
    for b in re.split(r"(?m)^(?=\S)", texto):
        b1 = " ".join(b.split())
        if "cwnd:" not in b1 and "rtt:" not in b1:
            continue
        d = {}
        m = re.search(r"\brtt:([\d.]+)/([\d.]+)", b1)
        if m:
            d["rtt_ms"], d["rttvar_ms"] = float(m.group(1)), float(m.group(2))
        for clave, pat in (("cwnd", r"\bcwnd:(\d+)"), ("unacked", r"\bunacked:(\d+)"),
                           ("snd_wnd", r"\bsnd_wnd:(\d+)"), ("mss", r"\bmss:(\d+)"),
                           ("bytes_acked", r"\bbytes_acked:(\d+)"), ("rto", r"\brto:(\d+)"),
                           ("rb", r"skmem:\([^)]*\brb(\d+)"), ("tb", r"skmem:\([^)]*\btb(\d+)"),
                           ("retrans", r"\bretrans:\d+/(\d+)")):
            m = re.search(pat, b1)
            if m:
                d[clave] = int(m.group(1))
        d.setdefault("unacked", 0)
        clave = d.get("bytes_acked", d.get("cwnd", 0))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, d)
    return mejor[1] if mejor else None


def leer_ss(path, t_ini, t_fin):
    filas = []
    if not Path(path).exists():
        return pd.DataFrame()
    for linea in open(path):
        r = json.loads(linea)
        if not (t_ini <= r["t"] <= t_fin):
            continue
        d = parse_ss(r["out"])
        if d:
            d["t"] = r["t"]
            filas.append(d)
    return pd.DataFrame(filas)


def leer_cola(path):
    """Serie de la cola del tbf: backlog (bytes), qlen (paquetes), drops y paquetes acumulados."""
    filas = []
    if not Path(path).exists():
        return pd.DataFrame()
    for linea in open(path):
        r = json.loads(linea)
        try:
            qs = json.loads(r["out"])
        except (json.JSONDecodeError, TypeError):
            continue
        for q in qs:
            if q.get("kind") == "tbf":
                filas.append(dict(t=r["t"], backlog=q.get("backlog", 0), qlen=q.get("qlen", 0),
                                  drops=q.get("drops", 0), packets=q.get("packets", 0)))
                break
    return pd.DataFrame(filas)


def diagnostico_ss(path):
    """Si ss.jsonl existe pero no se pudo extraer ningún socket, devuelve un texto con la salida cruda."""
    p = Path(path)
    if not p.exists():
        return "no existe ss.jsonl"
    lineas = [json.loads(l) for l in open(p) if l.strip()]
    if not lineas:
        return "ss.jsonl está vacío (el muestreador no escribió nada)"
    con_datos = [r for r in lineas if "cwnd:" in r.get("out", "")]
    if con_datos:
        return None
    rc = sorted({r.get("rc") for r in lineas})
    ej = next((r["out"] for r in lineas if r.get("out", "").strip()), "")
    return f"{len(lineas)} muestras, códigos de salida {rc}, ninguna con `cwnd:`. Salida cruda de ejemplo: {ej[:300]!r}"


def metricas_ss(ss):
    if len(ss) == 0:
        return {}
    mss = ss.mss.median() if "mss" in ss else 1448
    infl = ss.unacked.fillna(0) * mss
    out = dict(n_ss=len(ss), mss=mss, infl_max=infl.max(), infl_mean=infl.mean())
    if "rtt_ms" in ss:
        out["srtt"] = ss.rtt_ms.mean()
    if "rto" in ss:
        out["rto_ms"] = ss.rto.mean()
    if "cwnd" in ss:
        out["cwnd_mean"], out["cwnd_max"] = ss.cwnd.mean(), ss.cwnd.max()
    if "snd_wnd" in ss:
        out["snd_wnd"] = ss.snd_wnd.max()
    if "tb" in ss:
        out["tb"] = ss.tb.median()
    if "retrans" in ss:
        out["retrans"] = ss.retrans.max() - ss.retrans.min()
    return out


def rtt_iperf_ms(path):
    try:
        s = json.loads(Path(path).read_text())["end"]["streams"][0]["sender"]
        return s["mean_rtt"] / 1000 if s.get("mean_rtt") is not None else float("nan")
    except (KeyError, IndexError, json.JSONDecodeError):
        return float("nan")


def ventana_medicion(meta):
    """Intervalo de tiempo (epoch) dentro del test, sin el arranque -O ni el primer intervalo."""
    p, t0 = meta["params"], meta.get("t_iperf_inicio")
    if t0 is None:
        return None
    return t0 + p["omit"] + 1.5, t0 + p["omit"] + p["dur"]


def regresion_loglog(xs, ys):
    """Ajuste log y = a + b log x. Devuelve pendiente, IC95, p de H0: pendiente = 1, R², RMSE (log)."""
    lx, ly = np.log(np.asarray(xs, float)), np.log(np.asarray(ys, float))
    r = stats.linregress(lx, ly)
    n = len(lx)
    h = stats.t.ppf(1 - (1 - CONF) / 2, n - 2) * r.stderr
    t1 = (r.slope - 1) / r.stderr
    p1 = 2 * stats.t.sf(abs(t1), n - 2)
    rmse = float(np.sqrt(np.mean((ly - (r.intercept + r.slope * lx)) ** 2)))
    return dict(pend=r.slope, h=h, p1=p1, r2=r.rvalue ** 2, rmse=rmse, k=float(np.exp(r.intercept)), n=n)


# ----------------------------------------------------------------------------- T2: ventana fija
def an_ventana(res, frac_region=0.5):
    exp = "t2-ventana"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas, diag_ss = [], None
    for d, meta in corr:
        p = meta["params"]
        x = goodput_intervalos(d / "iperf.json")
        fila = dict(algo=p["algo"], wkb=p["window_kb"], W=p["window_kb"] * 1024, rtt=p["rtt"], cap_mbit=p["C"],
                    rep=rep_de(meta), x=x.mean(), rtt_iperf=rtt_iperf_ms(d / "iperf.json"),
                    rmem=meta.get("rmem_max"), wmem=meta.get("wmem_max"))
        v = ventana_medicion(meta)
        if v:
            m_ss = metricas_ss(leer_ss(d / "ss.jsonl", *v))
            fila.update(m_ss)
            if not m_ss and diag_ss is None:
                diag_ss = f"{d.name}: {diagnostico_ss(d / 'ss.jsonl')}"
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("srtt", "tb", "snd_wnd", "infl_max", "cwnd_max", "retrans"):
        if col not in df:
            df[col] = np.nan
    c_mbit = float(df.cap_mbit.iloc[0])
    cap = ETA * c_mbit
    df["x_win"] = df.W * 8 / (df.rtt / 1e3) / 1e6                     # Mbit/s según W y RTT configurado
    df["x_win_s"] = df.W * 8 / (df.srtt / 1e3) / 1e6                  # ... según el srtt del emisor
    df["ratio"] = df.x / df.x_win

    md = ["# T2 — Ventana fija: x = min(η·C, W/RTT)\n"]
    if diag_ss:
        md.append(f"**AVISO:** no se pudo leer el socket (`ss`) en alguna corrida. {diag_ss}\n")
    md.append(f"C = {c_mbit:.0f} Mbit/s, η·C = {cap:.2f} Mbit/s. W es la ventana pedida con `iperf3 -w` (en KiB). "
              "El buffer del tbf es de 4 MB, así que nunca hay pérdida y la cola puede crecer cuando W supera el BDP. "
              "`srtt` es el RTT suavizado del emisor, muestreado con `ss` cada 200 ms durante la medición.\n")
    wm = df.wmem.dropna().unique()
    rm = df.rmem.dropna().unique()
    if len(wm) or len(rm):
        md.append(f"Topes de buffer de socket vistos en los contenedores: wmem_max = {sorted(int(v) for v in wm)}, "
                  f"rmem_max = {sorted(int(v) for v in rm)}. Linux recorta lo que pide `-w` a estos valores "
                  f"(y luego duplica el resultado).\n")

    main = df[df.algo == "cubic"]
    cond = []
    for (wkb, rtt), g in main.groupby(["wkb", "rtt"]):
        m, h = ic_t(g.x)
        xw = g.x_win.iloc[0]
        teo = min(cap, xw)
        srtt = g.srtt.mean()
        teo_s = min(cap, g.W.iloc[0] * 8 / (srtt / 1e3) / 1e6) if srtt == srtt else float("nan")
        snd = g.snd_wnd.mean()
        teo_ef = min(cap, snd * 8 / (srtt / 1e3) / 1e6) if (snd == snd and srtt == srtt) else float("nan")
        q_pred = max(0.0, snd * 8 / (cap * 1e6) * 1e3 - rtt) if snd == snd else float("nan")
        cond.append(dict(wkb=wkb, rtt=rtt, n=len(g), x=m, h=h, xw=xw, teo=teo, err=100 * (m - teo) / teo,
                         snd=snd, teo_ef=teo_ef, err_ef=100 * (m - teo_ef) / teo_ef if teo_ef == teo_ef else float("nan"),
                         q_pred=q_pred, srtt=srtt, teo_s=teo_s, zona="ventana" if xw <= frac_region * cap else
                         ("enlace" if xw >= cap else "codo")))
    cdf = pd.DataFrame(cond)
    # ¿El sndbuf efectivo (tb de ss) sigue a la W pedida? Referencia: la razón tb/W de las dos ventanas más chicas.
    ref_tb = np.nan
    if main.tb.notna().any():
        chicas = main[main.wkb.isin(sorted(main.wkb.unique())[:2])]
        ref_tb = (chicas.tb / chicas.W).median()
    tb_cond = {k: (g.tb / g.W).mean() for k, g in main.groupby(["wkb", "rtt"])}
    cdf["tb_w"] = [tb_cond.get((r.wkb, r.rtt), np.nan) for r in cdf.itertuples()]
    cdf["recort"] = (cdf.tb_w < 0.9 * ref_tb) if ref_tb == ref_tb else False

    md.append("## Goodput por condición (algoritmo Cubic)\n")
    md.append("Media de repeticiones con IC95 t entre repeticiones. «Teórico» = min(η·C, W/RTT configurado). "
              f"Zona: *ventana* si W/RTT ≤ {frac_region:.0%}·η·C (región donde se ajusta la recta), "
              "*enlace* si W/RTT ≥ η·C, *codo* en el medio.\n")
    filas_c = [dict(w=int(r.wkb), rtt=int(r.rtt), n=int(r.n), x=f(r.x, 2), h=f(r.h, 2), xw=f(r.xw, 2),
                    teo=f(r.teo, 2), err=f(r.err, 1), tef=f(r.teo_ef, 2), eef=f(r.err_ef, 1), srtt=f(r.srtt, 2),
                    dq=f(r.srtt - r.rtt, 2), zona=r.zona, rc="sí" if r.recort else "") for r in cdf.sort_values(["rtt", "wkb"]).itertuples()]
    md.append("«Teórico con W efectiva» = min(η·C, snd_wnd/srtt), donde snd_wnd es la ventana que anuncia el receptor, "
              "medida con `ss` en el emisor. Sale de una medición distinta del goodput, así que es una prueba "
              "no circular de que el modelo vale con la ventana que Linux usa de verdad.\n")
    md.append(tabla_md(filas_c, [("w", "W (KiB)"), ("rtt", "RTT config (ms)"), ("n", "n"), ("x", "Goodput (Mbit/s)"),
                                  ("h", "± IC95"), ("xw", "W/RTT (Mbit/s)"), ("teo", "Teórico"),
                                  ("err", "Error %"), ("tef", "Teórico con W efectiva"), ("eef", "Error % (W efectiva)"),
                                  ("srtt", "srtt (ms)"), ("dq", "srtt − RTT config (ms)"),
                                  ("zona", "Zona"), ("rc", "Ventana recortada")]))
    if cdf.recort.any():
        md.append(f"**AVISO:** en {int(cdf.recort.sum())} condiciones el buffer de envío del socket (`tb`) no sigue "
                  "a la ventana pedida (queda por debajo del 90 % de la razón observada con las ventanas más chicas): "
                  "Linux recortó la ventana (tope `wmem_max`/`rmem_max`). Esas condiciones se marcan y se excluyen "
                  "del segundo ajuste de la sección siguiente.\n")

    md.append("## Ley de potencia: log x contra log(W/RTT), solo región limitada por ventana\n")
    reg_all = cdf[cdf.zona == "ventana"]
    filas_r = []
    for etiqueta, reg in (("todas", reg_all), ("sin recortadas", reg_all[~reg_all.recort])):
        if len(reg) < 3:
            continue
        for nombre, usa_cfg in (("RTT configurado", True), ("srtt del emisor", False)):
            xs = reg.xw if usa_cfg else reg.apply(lambda r: r.xw * r.rtt / r.srtt, axis=1)
            rr = regresion_loglog(xs, reg.x)
            filas_r.append(dict(c=etiqueta, n=nombre, N=rr["n"], pend=f(rr["pend"], 3), h=f(rr["h"], 3),
                                p1=pval(rr["p1"]), r2=f(rr["r2"], 5), rmse=f(rr["rmse"], 4), k=f(rr["k"], 3)))
    if filas_r:
        md.append(tabla_md(filas_r, [("c", "Condiciones"), ("n", "Variable explicativa"), ("N", "N"),
                                      ("pend", "Pendiente"), ("h", "± IC95"), ("p1", "p (H0: pendiente = 1)"),
                                      ("r2", "R²"), ("rmse", "RMSE (log)"), ("k", "Factor k (x ≈ k·W/RTT)")]))
        md.append("Unidad de ajuste: la media de cada condición (las repeticiones de una misma condición no son "
                  "puntos independientes de la recta). Con pendiente ≈ 1, k es la razón entre la ventana efectiva "
                  "y la pedida. Con 3 repeticiones y varianza casi nula, un p pequeño no implica un desvío "
                  "importante: mirar pendiente, IC y k. RMSE menor = la variable explica mejor.\n")
    else:
        md.append("_Hacen falta ≥ 3 condiciones en la región limitada por ventana._\n")

    md.append("## Ventana efectiva: ¿cuánta ventana usa Linux realmente?\n")
    md.append("Por cada W pedida (condiciones en zona *ventana*): «x·srtt/W» es la ventana efectiva por la ley de "
              "Little (bytes en vuelo = goodput × RTT) dividida por W; «sndbuf/W» es el buffer de envío del "
              "socket (`tb` de ss) y «snd_wnd/W» la ventana que anuncia el receptor.\n")
    filas_w = []
    for wkb, g in main[main.x_win <= frac_region * cap].groupby("wkb"):
        W = wkb * 1024
        little = (g.x * 1e6 / 8 * g.srtt / 1e3 / W)
        filas_w.append(dict(w=int(wkb), n=len(g), little=f(little.mean(), 2), tb=f(g.tb.mean() / W, 2),
                            snd=f(g.snd_wnd.mean() / W, 2), infl=f(g.infl_max.mean() / W, 2),
                            tbabs=f(g.tb.mean() / 1024, 0)))
    md.append(tabla_md(filas_w, [("w", "W pedida (KiB)"), ("n", "Corridas"), ("little", "x·srtt/W"),
                                  ("tb", "sndbuf/W"), ("snd", "snd_wnd/W"), ("infl", "máx en vuelo/W"),
                                  ("tbabs", "sndbuf (KiB)")]))
    md.append("Si «sndbuf/W» da ≈ 2, es el duplicado que hace el kernel. Si para W grandes el sndbuf deja de crecer, "
              "se llegó al tope wmem_max y la ventana pedida no es la usada.\n")

    md.append("## Régimen de enlace: RTT configurado vs srtt\n")
    enl = cdf[cdf.zona == "enlace"]
    if len(enl):
        filas_e = [dict(w=int(r.wkb), rtt=int(r.rtt), x=f(r.x, 2), teo_c=f(cap, 2), srtt=f(r.srtt, 2),
                        dq=f(r.srtt - r.rtt, 1), qp=f(r.q_pred, 1),
                        xs=f(r.x * 1e6 / 8 * r.srtt / 1e3 / (r.wkb * 1024), 2))
                  for r in enl.itertuples()]
        md.append(tabla_md(filas_e, [("w", "W (KiB)"), ("rtt", "RTT config"), ("x", "Goodput"), ("teo_c", "η·C"),
                                      ("srtt", "srtt (ms)"), ("dq", "Cola observada (ms) = srtt − RTT"),
                                      ("qp", "Cola predicha (ms) = snd_wnd/(η·C) − RTT"),
                                      ("xs", "x·srtt/W")]))
        md.append("La cola predicha usa solo la ventana anunciada y la capacidad (no el srtt), así que comparar con la "
                  "observada sí es una verificación independiente. ")
        md.append("Cuando W/RTT configurado supera η·C, el enlace manda: el goodput se clava en η·C y el exceso de "
                  "ventana se acumula como cola (srtt − RTT). Por la ley de Little, goodput × srtt = bytes en vuelo, "
                  "así que «x·srtt/W» es la ventana efectiva sobre la pedida y esa cuenta cierra siempre: "
                  "reformula el modelo, no lo verifica de forma independiente.\n")
    else:
        md.append("_No hay condiciones en régimen de enlace._\n")

    md.append("## ¿Importa el algoritmo cuando manda la ventana?\n")
    cond_comp = [k for k, g in df.groupby(["wkb", "rtt"]) if g.algo.nunique() > 1]
    cmp_df = df[[(w, r) in cond_comp for w, r in zip(df.wkb, df.rtt)]].copy()
    if len(cmp_df):
        cmp_df["cond"] = cmp_df.wkb.astype(str) + "k_" + cmp_df.rtt.astype(str) + "ms"
        filas_a = []
        for (cnd, algo), g in cmp_df.groupby(["cond", "algo"]):
            m, h = ic_t(g.ratio)
            filas_a.append(dict(c=cnd, a=algo, n=len(g), r=f(m, 4), h=f(h, 4), x=f(g.x.mean(), 2)))
        md.append("Razón x / (W/RTT configurado) por condición y algoritmo (1 = el modelo se cumple exacto).\n")
        md.append(tabla_md(filas_a, [("c", "Condición (W_RTT)"), ("a", "Algoritmo"), ("n", "n"),
                                      ("r", "Razón media"), ("h", "± IC95"), ("x", "Goodput (Mbit/s)")]))
        try:
            nmin = cmp_df.groupby(["cond", "algo"]).size().min()
            if nmin >= 2 and cmp_df.algo.nunique() > 1:
                modelo = smf.ols("ratio ~ C(algo) + C(cond)", data=cmp_df).fit()
                an = sm.stats.anova_lm(modelo, typ=2)
                md.append("ANOVA (efecto aditivo del algoritmo, controlando por condición):\n")
                md.append(tabla_md([dict(fu=i, F=f(r["F"], 2), p=pval(r["PR(>F)"])) for i, r in an.iterrows()],
                                   [("fu", "Fuente"), ("F", "F"), ("p", "p")]))
                sw = stats.shapiro(modelo.resid)
                lev = stats.levene(*[g.ratio.values for _, g in cmp_df.groupby(["cond", "algo"])])
                kw = stats.kruskal(*[g.ratio.values for _, g in cmp_df.groupby("algo")])
                md.append(f"Supuestos: Shapiro-Wilk de residuos p = {pval(sw.pvalue)}, Levene p = {pval(lev.pvalue)}. "
                          f"Kruskal-Wallis entre algoritmos (sobre la razón normalizada): p = {pval(kw.pvalue)} "
                          f"(cada algoritmo reúne {int(cmp_df.groupby('algo').size().min())} observaciones, "
                          f"todas las condiciones juntas).\n")
        except Exception as e:     # pragma: no cover
            md.append(f"_No se pudo calcular el ANOVA: {e}_\n")
    else:
        md.append("_Sin condiciones con más de un algoritmo._\n")

    md.append(seccion_cpu(corr))

    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.5))
    for rtt, g in main.groupby("rtt"):
        ax[0].scatter(g.x_win, g.x, s=18, label=f"RTT {rtt} ms")
    xs = np.logspace(np.log10(max(df.x_win.min(), 0.5)), np.log10(df.x_win.max() * 1.1), 50)
    ax[0].plot(xs, np.minimum(xs, cap), "k--", label="min(η·C, W/RTT)")
    ax[0].set(xscale="log", yscale="log", xlabel="W/RTT configurado (Mbit/s)", ylabel="Goodput medido (Mbit/s)",
              title="Ventana fija: medido vs modelo")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=.3, which="both")
    for rtt, g in cdf.groupby("rtt"):
        g = g.sort_values("wkb")
        ax[1].plot(g.wkb, g.srtt - g.rtt, marker="o", label=f"RTT {rtt} ms")
    ax[1].set(xscale="log", xlabel="W (KiB)", ylabel="srtt − RTT configurado (ms)",
              title="Cola que aparece al pasar el BDP")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=.3, which="both")
    fig.tight_layout()
    fig.savefig(res / exp / "ventana.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T2: ventana libre, buffer = BDP
def an_libre(res):
    exp = "t2-libre"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas, series, diag_ss = [], {}, None
    for d, meta in corr:
        p = meta["params"]
        x = goodput_intervalos(d / "iperf.json")
        C = p["C"]
        bdp = C * 1e6 / 8 * p["rtt"] / 1000
        fila = dict(algo=p["algo"], rep=rep_de(meta), x=x.mean(), util=100 * x.mean() / (ETA * C), bdp=bdp, C=C)
        v = ventana_medicion(meta)
        if v:
            m_ss = metricas_ss(leer_ss(d / "ss.jsonl", *v))
            fila.update(m_ss)
            if not m_ss and diag_ss is None:
                diag_ss = f"{d.name}: {diagnostico_ss(d / 'ss.jsonl')}"
            q = leer_cola(d / "cola.jsonl")
            if len(q):
                w = q[(q.t >= v[0]) & (q.t <= v[1])]
                if len(w) >= 2:
                    fila.update(q_mean=w.backlog.mean(), q_p95=w.backlog.quantile(.95), q_max=w.backlog.max(),
                                q_ms=w.backlog.mean() * 8 / (C * 1e6) * 1e3,
                                perd=100 * (w.drops.iloc[-1] - w.drops.iloc[0]) /
                                max(w.packets.iloc[-1] - w.packets.iloc[0] + w.drops.iloc[-1] - w.drops.iloc[0], 1),
                                n_q=len(w))
                    series[(p["algo"], rep_de(meta))] = ((w.t - v[0]).to_numpy(), w.backlog.to_numpy() / bdp)
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("q_mean", "q_p95", "q_max", "q_ms", "perd", "srtt", "retrans", "cwnd_mean"):
        if col not in df:
            df[col] = np.nan
    bdp = df.bdp.iloc[0]
    md = ["# T2 — Ventana libre con buffer = BDP\n"]
    if diag_ss:
        md.append(f"**AVISO:** no se pudo leer el socket (`ss`) en alguna corrida. {diag_ss}\n")
    md.append(f"RTT = 20 ms, C = {df.C.iloc[0]:.0f} Mbit/s, buffer del tbf = BDP = {bdp/1000:.0f} kB. Sin `-w`: la ventana "
              "la gobierna el algoritmo. La cola es el backlog del tbf (`tc -s -j`, cada 100 ms); la unidad "
              "estadística es la media de cada corrida (las muestras de una corrida están correlacionadas), "
              "con IC95 t entre repeticiones.\n")
    filas_m = []
    for algo, g in df.groupby("algo"):
        u, hu = ic_t(g.util)
        qm, hq = ic_t(g.q_mean.dropna()) if g.q_mean.notna().sum() else (float("nan"), float("nan"))
        qd, hd_ = ic_t(g.q_ms.dropna()) if g.q_ms.notna().sum() else (float("nan"), float("nan"))
        pe, hp = ic_t(g.perd.dropna()) if g.perd.notna().sum() else (float("nan"), float("nan"))
        filas_m.append(dict(a=algo, n=len(g), u=f(u, 2), hu=f(hu, 2), qm=f(qm / 1000, 1), hq=f(hq / 1000, 1),
                            qb=f(qm / bdp, 2), qd=f(qd, 2), hd=f(hd_, 2), p95=f(g.q_p95.mean() / bdp, 2),
                            mx=f(g.q_max.mean() / bdp, 2), pe=f(pe, 3), hp=f(hp, 3), sr=f(g.srtt.mean(), 2)))
    md.append(tabla_md(filas_m, [("a", "Algoritmo"), ("n", "n"), ("u", "Utilización (% de η·C)"), ("hu", "± IC95"),
                                  ("qm", "Cola media (kB)"), ("hq", "± IC95"), ("qb", "Cola media / BDP"),
                                  ("qd", "Retardo de cola medio (ms)"), ("hd", "± IC95"), ("p95", "Cola p95 / BDP"),
                                  ("mx", "Cola máx / BDP"), ("pe", "Pérdida en el tbf (%)"), ("hp", "± IC95"),
                                  ("sr", "srtt medio (ms)")]))
    md.append("«Utilización» es el goodput sobre η·C: 100 % = enlace lleno. La pérdida es la fracción de "
              "paquetes que el tbf descartó por buffer lleno durante la medición.\n")
    md.append("## Por repetición\n")
    md.append(tabla_md([dict(a=r.algo, rep=int(r.rep), u=f(r.util, 2), q=f(r.q_mean / bdp, 3),
                             pe=f(r.perd, 3), rt=f(r.retrans, 0)) for r in df.sort_values(["algo", "rep"]).itertuples()],
                       [("a", "Algoritmo"), ("rep", "Rep"), ("u", "Utilización %"), ("q", "Cola media / BDP"),
                        ("pe", "Pérdida %"), ("rt", "Retransmisiones en la ventana")]))
    md.append("Con 3 repeticiones por algoritmo, el menor p posible de Kruskal-Wallis es 0.027: "
              "la comparación entre algoritmos se apoya en los IC, no en un único p.\n")
    corr_ok = [c for c in corr]
    md.append(seccion_cpu(corr_ok))

    algos = sorted(df.algo.unique())
    fig, ax = plt.subplots(1, len(algos), figsize=(4.6 * len(algos), 3.8), sharey=True, squeeze=False)
    for k, algo in enumerate(algos):
        reps = sorted(r for (a_, r) in series if a_ == algo)
        if reps:
            t, q = series[(algo, reps[0])]
            ax[0][k].plot(t, q, lw=.8)
        ax[0][k].axhline(1, color="r", ls="--", lw=.8, label="buffer = BDP")
        ax[0][k].set(title=f"{algo}: cola del tbf (rep 1)", xlabel="tiempo (s)")
        ax[0][k].grid(alpha=.3)
    ax[0][0].set_ylabel("cola / BDP")
    ax[0][0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(res / exp / "cola.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T3: utilidades
def modelo_cache(res, clave, fn):
    """Los modelos de fluido tardan unos segundos: se guardan en results/_cache_modelos.json."""
    ruta = res / "_cache_modelos.json"
    cache = json.loads(ruta.read_text()) if ruta.exists() else {}
    k = f"v{modelos.MODELOS_VERSION}|{clave}"
    if k not in cache:
        cache[k] = fn()
        ruta.write_text(json.dumps(cache))
    return cache[k]


def _solo_numeros(d):
    return {k: float(v) for k, v in d.items() if k != "serie"}


def fluido_uno_c(res, algo, c_mbit, rtt_ms, b):
    bdp = modelos.bdp_bytes(c_mbit, rtt_ms)
    return modelo_cache(res, f"uno|{algo}|{c_mbit}|{rtt_ms}|{b}",
                        lambda: _solo_numeros(modelos.fluido_uno(algo, c_mbit, rtt_ms, b * bdp)))


def fluido_n_c(res, n, c_mbit, rtt_ms, b, politica, k=None):
    bdp = modelos.bdp_bytes(c_mbit, rtt_ms)
    clave = f"n|{n}|{politica}|{c_mbit}|{rtt_ms}|{b}" + (f"|k{k:.2f}" if k is not None else "")
    return modelo_cache(res, clave, lambda: _solo_numeros(modelos.fluido_n(n, c_mbit, rtt_ms, b * bdp, politica, k=k)))


def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f(100 * x, nd)


def ventana_cola(d, meta, bdp):
    """Estadísticas de la cola del tbf dentro de la ventana de medición."""
    v = ventana_medicion(meta)
    q = leer_cola(d / "cola.jsonl") if v else pd.DataFrame()
    if not v or len(q) == 0:
        return {}, None
    w = q[(q.t >= v[0]) & (q.t <= v[1])]
    if len(w) < 2:
        return {}, None
    dur = w.t.iloc[-1] - w.t.iloc[0]
    c_mbit = meta["params"]["C"]
    out = dict(q_mean=w.backlog.mean(), q_bdp=w.backlog.mean() / bdp, q_max_bdp=w.backlog.max() / bdp,
               q_ms=w.backlog.mean() * 8 / (c_mbit * 1e6) * 1e3, drops_s=(w.drops.iloc[-1] - w.drops.iloc[0]) / dur,
               perd=100 * (w.drops.iloc[-1] - w.drops.iloc[0]) /
               max(w.packets.iloc[-1] - w.packets.iloc[0] + w.drops.iloc[-1] - w.drops.iloc[0], 1))
    return out, ((w.t - v[0]).to_numpy(), w.backlog.to_numpy() / bdp)


def goodput_por_flujo(path):
    """Mbit/s medios de cada flujo (sin -O ni el primer intervalo)."""
    j = json.loads(Path(path).read_text())
    ints = [i for i in j["intervals"]
            if not (i["sum"].get("omitted", False) or any(s.get("omitted") for s in i.get("streams", [])))][1:]
    mat = np.array([[s["bits_per_second"] for s in i["streams"]] for i in ints]) / 1e6
    return mat.mean(axis=0)


def jain(x):
    x = np.asarray(x, float)
    return float(x.sum() ** 2 / (len(x) * (x ** 2).sum()))


def leer_sst(path, t_ini, t_fin):
    """Ventanas (cwnd) de cada flujo de datos. Devuelve (t, matriz muestras x flujos, puertos) o None."""
    if not Path(path).exists():
        return None
    tiempos, filas = [], []
    for linea in open(path):
        r = json.loads(linea)
        if t_ini <= r["t"] <= t_fin:
            tiempos.append(r["t"])
            filas.append({fl[0]: fl for fl in r["f"]})
    if not filas:
        return None
    cuenta = {}
    for dct in filas:
        for p in dct:
            cuenta[p] = cuenta.get(p, 0) + 1
    puertos = sorted(p for p, c in cuenta.items() if c >= 0.9 * len(filas))
    if len(puertos) < 2:
        return None
    M = np.full((len(filas), len(puertos)), np.nan)
    for i, dct in enumerate(filas):
        for j, p in enumerate(puertos):
            if p in dct and dct[p][2] is not None:
                M[i, j] = dct[p][2]
    for j in range(M.shape[1]):
        M[:, j] = pd.Series(M[:, j]).ffill().bfill().to_numpy()
    return np.array(tiempos), M, puertos


def sincronia(M, n_perm=2000, seed=1):
    """Sincronización entre flujos a partir de sus ventanas (muestras x flujos).
      rho = CV de la ventana total · √N / CV medio de un flujo:  ≈ 1 si los flujos son independientes,
            ≈ √N si están en fase (la suma se comporta como un solo diente de sierra).
      r   = correlación media entre pares de flujos; su p sale de desplazar circularmente la serie de cada
            flujo un tiempo al azar (conserva la forma y la periodicidad de cada flujo y destruye la fase
            relativa): p = P(r_nula >= r_observada)."""
    T, N = M.shape
    cv_flujo = float(np.mean(M.std(axis=0, ddof=1) / M.mean(axis=0)))
    tot = M.sum(axis=1)
    rho = float(tot.std(ddof=1) / tot.mean() * np.sqrt(N) / cv_flujo)
    iu = np.triu_indices(N, 1)

    def rmed(X):
        return float(np.nanmean(np.corrcoef(X.T)[iu]))
    r_obs = rmed(M)
    rng = np.random.default_rng(seed)
    nulos = np.array([rmed(np.column_stack([np.roll(M[:, j], int(rng.integers(T))) for j in range(N)]))
                      for _ in range(n_perm)])
    return dict(rho=rho, r=r_obs, r_nulo=float(nulos.mean()), p=float((1 + np.sum(nulos >= r_obs)) / (1 + n_perm)))


def eventos_reduccion(c, umbral, refractario=1):
    """Cuenta reducciones bruscas de la ventana: cwnd[i] < (1 − umbral)·cwnd[i−1]. Un evento de pérdida reduce la
    ventana de golpe (Reno ×0.5, Cubic ×0.7) y el crecimiento entre dos muestras es mucho menor. Muestras
    consecutivas dentro del período refractario cuentan como un solo evento. Es una cuenta aproximada: depende
    de la resolución del muestreo (200 ms en t3-uno, 50 ms en t3-n)."""
    c = np.asarray(c, float)
    n, ult = 0, -10 ** 9
    for i in range(1, len(c)):
        if c[i - 1] > 0 and c[i] < (1 - umbral) * c[i - 1] and i - ult > refractario:
            n += 1
            ult = i
    return n


UMBRAL_REDUCCION = {"reno": 0.25, "cubic": 0.15}      # Reno cae a ×0.5; Cubic a ×0.7


def anclas_sincronia(res, n, c_mbit, rtt_ms, b):
    """ρ y r que dan los propios modelos de fluido (a partir de sus ventanas por flujo)."""
    def calc():
        out = {}
        for pol in ("sync", "desync"):
            s = modelos.fluido_n(n, c_mbit, rtt_ms, b * modelos.bdp_bytes(c_mbit, rtt_ms), pol, t_total=60,
                                 serie_s=0.05, t_serie=30)
            r = sincronia(np.array(s["serie"][2]), n_perm=20)
            out[pol] = dict(rho=float(r["rho"]), r=float(r["r"]))
        return out
    return modelo_cache(res, f"anclas|{n}|{c_mbit}|{rtt_ms}|{b}", calc)


def matriz_eventos(M, umbral=0.25):
    """Matriz booleana muestras x flujos: True donde la ventana de ese flujo cae bruscamente."""
    T, N = M.shape
    E = np.zeros((T, N), bool)
    for j in range(N):
        ult = -10 ** 9
        for i in range(1, T):
            if M[i - 1, j] > 0 and M[i, j] < (1 - umbral) * M[i - 1, j] and i - ult > 1:
                E[i, j] = True
                ult = i
    return E


def flujos_por_episodio(E):
    """Un episodio = eventos encadenados con huecos de a lo sumo 2 muestras. Devuelve, por episodio, cuántos
    flujos distintos redujeron su ventana."""
    tam, grupo = [], []
    for t in np.where(E.any(axis=1))[0]:
        if grupo and t - grupo[-1] > 2:
            tam.append(int(E[grupo].any(axis=0).sum()))
            grupo = []
        grupo.append(t)
    if grupo:
        tam.append(int(E[grupo].any(axis=0).sum()))
    return np.array(tam)


def coincidencia_eventos(M, duracion, ventana=1, n_perm=500, n_perm_k=100, seed=1):
    """¿Los eventos de pérdida de distintos flujos coinciden más que por azar?
      coinc = pares de eventos de flujos distintos separados <= `ventana` muestras (observados) / (esperados si se
              desplaza circularmente la serie de eventos de cada flujo un tiempo al azar). ≈ 1: independientes.
      k_med / k_azar = flujos distintos por episodio, medido y esperado por azar (si k_azar ≈ N el azar satura
              los episodios y k_med no es informativo)."""
    E = matriz_eventos(M)
    T, N = E.shape
    if E.sum() < 5:
        return {}
    ker = np.ones(2 * ventana + 1)
    D = np.column_stack([np.convolve(E[:, j].astype(float), ker, mode="same") > 0 for j in range(N)])

    def total(E_, D_):
        return float((E_ * (D_.sum(axis=1, keepdims=True) - D_)).sum())
    obs = total(E, D)
    rng = np.random.default_rng(seed)
    nulos = np.empty(n_perm)
    k_nulo = []
    for p in range(n_perm):
        sh = rng.integers(T, size=N)
        Ep = np.column_stack([np.roll(E[:, j], sh[j]) for j in range(N)])
        Dp = np.column_stack([np.roll(D[:, j], sh[j]) for j in range(N)])
        nulos[p] = total(Ep, Dp)
        if p < n_perm_k:
            k_nulo.append(flujos_por_episodio(Ep).mean())
    tam = flujos_por_episodio(E)
    return dict(coinc=obs / nulos.mean(), coinc_p=float((1 + np.sum(nulos >= obs)) / (1 + n_perm)),
                n_ev=int(E.sum()), k_med=float(tam.mean()), k_azar=float(np.mean(k_nulo)), eps_s=len(tam) / duracion)


def anova_dos_factores(df, resp, f1, f2, md):
    """ANOVA de dos factores + supuestos, como en T1 (columnas con nombres que no choquen con C())."""
    celdas = df.groupby([f1, f2])
    if len(df) > celdas.ngroups and celdas.size().min() >= 2 and df[f1].nunique() > 1:
        try:
            modelo = smf.ols(f"{resp} ~ C({f1}) * C({f2})", data=df).fit()
            an = sm.stats.anova_lm(modelo, typ=2)
            md.append(tabla_md([dict(fu=i, F=f(r["F"], 2), p=pval(r["PR(>F)"])) for i, r in an.iterrows()],
                               [("fu", "Fuente"), ("F", "F"), ("p", "p")]))
            sw = stats.shapiro(modelo.resid)
            lev = stats.levene(*[g[resp].values for _, g in celdas])
            kw = stats.kruskal(*[g[resp].values for _, g in df.groupby(f1)])
            md.append(f"Supuestos: Shapiro-Wilk de residuos p = {pval(sw.pvalue)}; Levene p = {pval(lev.pvalue)}. "
                      f"Si alguno es < 0.05, el ANOVA no es confiable: Kruskal-Wallis entre «{f1}» "
                      f"(grupos de {int(df.groupby(f1).size().min())} observaciones): p = {pval(kw.pvalue)}.\n")
        except Exception as e:      # pragma: no cover
            md.append(f"_No se pudo calcular el ANOVA: {e}_\n")
    else:
        md.append("_Hacen falta ≥ 2 repeticiones por celda para el ANOVA._\n")


# ----------------------------------------------------------------------------- T3: un flujo
def an_t3_uno(res):
    exp = "t3-uno"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas, trazas, diag = [], {}, None
    for d, meta in corr:
        p = meta["params"]
        x = goodput_intervalos(d / "iperf.json")
        c_mbit = p["C"]
        bdp = modelos.bdp_bytes(c_mbit, p["rtt"])
        fila = dict(algo=p["algo"], b=p["b"], rep=rep_de(meta), buf=p["buf"], bdp=bdp,
                    util=100 * x.mean() / (ETA * c_mbit))
        cq, tr = ventana_cola(d, meta, bdp)
        fila.update(cq)
        if tr is not None:
            trazas[(p["algo"], p["b"], rep_de(meta))] = tr
        v = ventana_medicion(meta)
        if v:
            ss_df = leer_ss(d / "ss.jsonl", *v)
            m_ss = metricas_ss(ss_df)
            fila.update(m_ss)
            if m_ss.get("retrans") is not None:
                fila["retrans_s"] = m_ss["retrans"] / (v[1] - v[0])
                if p["algo"] in UMBRAL_REDUCCION and "cwnd" in ss_df and len(ss_df) > 2:
                    t_ss = ss_df.t.to_numpy()
                    fila["red_s"] = eventos_reduccion(ss_df.cwnd.to_numpy(), UMBRAL_REDUCCION[p["algo"]]) / (t_ss[-1] - t_ss[0])
            elif diag is None:
                diag = f"{d.name}: {diagnostico_ss(d / 'ss.jsonl')}"
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("q_bdp", "q_ms", "drops_s", "perd", "srtt", "retrans_s", "red_s"):
        if col not in df:
            df[col] = np.nan
    c_mbit, rtt = corr[0][1]["params"]["C"], corr[0][1]["params"]["rtt"]
    bdp = modelos.bdp_bytes(c_mbit, rtt)

    md = ["# T3 — Un flujo: utilización y cola vs tamaño del buffer\n"]
    if diag:
        md.append(f"**AVISO:** no se pudo leer el socket (`ss`) en alguna corrida. {diag}\n")
    md.append(f"C = {c_mbit} Mbit/s, RTT = {rtt} ms, BDP = C·RTT = {bdp/1000:.0f} kB; b = B/BDP. "
              "«Utilización» = goodput / (η·C). Cada corrida aporta una media (la medición dura 60 s después de "
              "descartar el arranque); los IC95 son t de Student entre repeticiones.\n")
    md.append("**Modelos.** *Cerrado (Reno)*: U(b) = ¾(1+b)²/(1+b+b²) si b < 1 y U = 1 si b ≥ 1; cola media "
              "q/BDP = b²(3+2b)/(3(1+b+b²)) si b < 1 y 7(1+b)/9 − 1 si b ≥ 1 (deducción en `modelos.py`). "
              "*Fluido*: integra la ventana con RTT(t) = RTT₀ + cola/C y pérdida cuando la cola llega a B "
              "(Reno: β = 0.5; Cubic: β = 0.7, C = 0.4, región TCP-friendly, sin fast convergence). "
              "El fluido se validó contra las fórmulas cerradas antes de usarse (`python modelos.py`). "
              "BBR no tiene modelo.\n")

    grupos = list(df.groupby(["algo", "b"]))
    tA, tB, tC = [], [], []
    for (algo, b), g in sorted(grupos, key=lambda t: (t[0][0], t[0][1])):
        m, h = ic_t(g.util)
        cer = 100 * modelos.u_reno(b) if algo == "reno" else None
        fl = fluido_uno_c(res, algo, c_mbit, rtt, b) if algo in ("reno", "cubic") else None
        ref = 100 * fl["util"] if fl else None
        t_p = stats.ttest_1samp(g.util, ref).pvalue if (fl and len(g) >= 2) else float("nan")
        tA.append(dict(a=algo, b=b, B=f(g.buf.iloc[0] / 1000, 1), n=len(g), u=f(m, 2), h=f(h, 2),
                       cer=f(cer, 2) if cer is not None else "—", fl=f(ref, 2) if fl else "—",
                       err=f(100 * (m - ref) / ref, 1) if fl else "—", p=pval(t_p)))
        qm, hq = ic_t(g.q_bdp.dropna()) if g.q_bdp.notna().sum() else (float("nan"), float("nan"))
        tB.append(dict(a=algo, b=b, q=f(qm, 3), hq=f(hq, 3),
                       cer=f(modelos.cola_reno(b), 3) if algo == "reno" else "—",
                       fl=f(fl["q_bdp"], 3) if fl else "—", qms=f(g.q_ms.mean(), 2), sr=f(g.srtt.mean(), 2),
                       qmx=f(g.q_max_bdp.mean(), 2) if "q_max_bdp" in g else "—"))
        rd = g.red_s.mean() if g.red_s.notna().any() else float("nan")
        tC.append(dict(a=algo, b=b, dr=f(g.drops_s.mean(), 2), rt=f(g.retrans_s.mean(), 2),
                       rd=f(rd, 2) if rd == rd else "—", ev=f(fl["eventos_s"], 2) if fl else "—",
                       rz=f(rd / fl["eventos_s"], 2) if (fl and rd == rd and fl["eventos_s"] > 0) else "—",
                       dpe=f(g.drops_s.mean() / rd, 1) if (rd == rd and rd > 0) else "—",
                       ci=f(fl["ciclo_s"], 2) if fl else "—", pe=f(g.perd.mean(), 3)))

    md.append("## Utilización\n")
    md.append(tabla_md(tA, [("a", "Algoritmo"), ("b", "b = B/BDP"), ("B", "B (kB)"), ("n", "n"),
                             ("u", "Utilización medida (%)"), ("h", "± IC95"), ("cer", "Cerrado Reno (%)"),
                             ("fl", "Fluido (%)"), ("err", "Error % vs fluido"), ("p", "p (t)")]))
    md.append("El error % es lo que importa en la práctica: con varianzas tan chicas, casi cualquier diferencia "
              "sale «significativa». Un error negativo grande a b chico significa que el algoritmo real rinde "
              "menos que el diente de sierra ideal.\n")
    md.append("## Cola\n")
    md.append(tabla_md(tB, [("a", "Algoritmo"), ("b", "b"), ("q", "Cola media / BDP medida"), ("hq", "± IC95"),
                             ("cer", "Cerrado Reno"), ("fl", "Fluido"), ("qms", "Retardo de cola medio (ms)"),
                             ("sr", "srtt medio (ms)"), ("qmx", "Cola máx / BDP")]))
    md.append("## Eventos de pérdida: ¿ciclos reales vs ciclos del modelo?\n")
    md.append("Un evento de pérdida puede descartar varios paquetes seguidos, así que los descartes por segundo no "
              "se pueden comparar con los ciclos del modelo. Lo que sí cuenta eventos son las **reducciones bruscas de la "
              "ventana** vistas en `ss` (cwnd cae más de 25 % entre dos muestras para Reno, más de 15 % para Cubic; "
              "muestreo de 200 ms, cuenta aproximada). Si «reducciones medidas / ciclos del fluido» ≈ 1, las pérdidas "
              "ocurren una por ciclo como supone el modelo; si es claramente > 1, la cola se desborda antes de lo previsto "
              "(ráfagas, que el modelo no tiene) o hay timeouts. «Descartes por evento» dice cuántos paquetes se pierden en cada uno. "
              "BBR no reduce la ventana a escalones, por eso no tiene esta columna.\n")
    md.append(tabla_md(tC, [("a", "Algoritmo"), ("b", "b"), ("dr", "Descartes del tbf (por s)"),
                             ("rt", "Retransmisiones (por s, ss)"), ("rd", "Reducciones de ventana (por s, ss)"),
                             ("ev", "Ciclos por s del fluido"), ("rz", "Reducciones medidas / ciclos del fluido"),
                             ("dpe", "Descartes por evento"), ("ci", "Ciclo del fluido (s)"),
                             ("pe", "Pérdida en el tbf (%)")]))

    md.append("## ANOVA de dos factores (utilización: algoritmo × b)\n")
    df["bf"] = df.b.astype(str)
    anova_dos_factores(df, "util", "algo", "bf", md)
    md.append(seccion_cpu(corr))

    # ---- gráficos
    marcas = {"reno": "o", "cubic": "s", "bbr": "^"}
    bs = sorted(df.b.unique())
    bfino = np.logspace(np.log10(min(bs)), np.log10(max(bs)), 60)
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6))
    ax[0].plot(bfino, [100 * modelos.u_reno(x) for x in bfino], "k-", lw=1, label="cerrado Reno")
    ax[1].plot(bfino, [modelos.cola_reno(x) for x in bfino], "k-", lw=1, label="cerrado Reno")
    for algo, est in (("reno", "--"), ("cubic", ":")):
        if (df.algo == algo).any():
            fl = [fluido_uno_c(res, algo, c_mbit, rtt, x) for x in bs]
            ax[0].plot(bs, [100 * z["util"] for z in fl], est, marker="x", color="gray", label=f"fluido {algo}")
            ax[1].plot(bs, [z["q_bdp"] for z in fl], est, marker="x", color="gray", label=f"fluido {algo}")
    for algo, g0 in df.groupby("algo"):
        pts = [(b, *ic_t(g.util), *(ic_t(g.q_bdp.dropna()) if g.q_bdp.notna().sum() else (np.nan, np.nan)))
               for b, g in g0.groupby("b")]
        bb, um, uh, qm, qh = map(np.array, zip(*pts))
        ax[0].errorbar(bb, um, yerr=np.nan_to_num(uh), marker=marcas.get(algo, "o"), capsize=3, ls="none", label=f"medido {algo}")
        ax[1].errorbar(bb, qm, yerr=np.nan_to_num(qh), marker=marcas.get(algo, "o"), capsize=3, ls="none", label=f"medido {algo}")
    ax[0].set(xscale="log", xlabel="b = B/BDP", ylabel="Utilización (%)", title="Utilización vs buffer (un flujo)")
    ax[1].set(xscale="log", yscale="log", xlabel="b = B/BDP", ylabel="Cola media / BDP", title="Cola media vs buffer")
    for x_ in ax:
        x_.grid(alpha=.3, which="both")
        x_.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(res / exp / "buffer_uno.png", dpi=130)
    plt.close(fig)

    b_traza = min(bs, key=lambda x: abs(x - 0.5))
    algos_t = [x for x in ("reno", "cubic") if (x, b_traza, 1) in trazas]
    if algos_t:
        fig, ax = plt.subplots(1, len(algos_t), figsize=(6 * len(algos_t), 3.8), sharey=True, squeeze=False)
        for k, algo in enumerate(algos_t):
            t, q = trazas[(algo, b_traza, 1)]
            sel = t <= 20
            ax[0][k].plot(t[sel], q[sel], lw=.9, label="medido (tbf, rep 1)")
            ts, qs, _ = modelos.fluido_uno(algo, c_mbit, rtt, b_traza * bdp, serie_s=0.05, t_serie=20)["serie"]
            ax[0][k].plot(ts, qs, lw=.9, alpha=.8, label="fluido (fase arbitraria)")
            ax[0][k].axhline(b_traza, color="r", ls="--", lw=.8)
            ax[0][k].set(title=f"{algo}, b = {b_traza}: cola / BDP", xlabel="tiempo (s)")
            ax[0][k].grid(alpha=.3)
            ax[0][k].legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(res / exp / "cola_traza.png", dpi=130)
        plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T3: N flujos Reno
def an_t3_n(res):
    exp = "t3-n"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas, trazas = [], {}
    for d, meta in corr:
        p = meta["params"]
        N = p["flows"]
        x = goodput_intervalos(d / "iperf.json")
        c_mbit = p["C"]
        bdp = modelos.bdp_bytes(c_mbit, p["rtt"])
        por_flujo = goodput_por_flujo(d / "iperf.json")
        fila = dict(N=N, b=p["b"], rep=rep_de(meta), buf=p["buf"], util=100 * x.mean() / (ETA * c_mbit),
                    jain=jain(por_flujo), flujos_iperf=len(por_flujo))
        cq, tr = ventana_cola(d, meta, bdp)
        fila.update(cq)
        v = ventana_medicion(meta)
        if v:
            s = leer_sst(d / "sst.jsonl", *v)
            if s is not None:
                t, M, puertos = s
                fila["flujos_sst"] = len(puertos)
                fila["cwnd_medio"] = float(M.mean())
                fila.update(sincronia(M))
                fila.update(coincidencia_eventos(M, t[-1] - t[0]))
                fila["red_s"] = sum(eventos_reduccion(M[:, j], UMBRAL_REDUCCION["reno"]) for j in range(M.shape[1])) \
                    / (t[-1] - t[0])
                trazas[(N, p["b"], rep_de(meta))] = (t - v[0], M)
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("q_bdp", "drops_s", "rho", "r", "p", "flujos_sst", "cwnd_medio", "red_s", "coinc", "coinc_p",
                "k_med", "k_azar", "eps_s"):
        if col not in df:
            df[col] = np.nan
    c_mbit, rtt = corr[0][1]["params"]["C"], corr[0][1]["params"]["rtt"]
    bdp = modelos.bdp_bytes(c_mbit, rtt)

    md = ["# T3 — N flujos Reno: regla BDP/√N y sincronización\n"]
    md.append(f"C = {c_mbit} Mbit/s, RTT = {rtt} ms, BDP = {bdp/1000:.0f} kB, N flujos Reno (`iperf3 -P N`), "
              "60 s de medición. IC95 t entre repeticiones.\n")
    md.append("**Modelos de comparación.** *Fluido sincronizado*: en cada pérdida los N flujos reducen su ventana "
              "(la suma se comporta como un solo diente de sierra: da lo mismo que un flujo con el mismo b). "
              "*Fluido «un flujo por pérdida»*: en cada pérdida reduce un solo flujo, elegido en proporción a su ventana; "
              "es el extremo más regulado: la ventana total casi no oscila (sus ρ y r salen en la tabla de sincronización, "
              "con r negativa). Entre los dos queda el caso de fases independientes (ρ ≈ 1, r ≈ 0), que es el supuesto de "
              "Appenzeller y no coincide con ninguno de los dos modelos de fluido. *Cota de Appenzeller et al. 2004 (Eq. 3)*: "
              "Util ≥ erf(3√3/(2√2) · √N · b/(1+b)); es una cota inferior que supone muchos flujos independientes "
              "(el artículo la valida con cientos de flujos y dice que con menos de ~100 hay sincronización parcial).\n")

    filas_t = []
    for (N, b), g in sorted(df.groupby(["N", "b"])):
        m, h = ic_t(g.util)
        s_ = fluido_n_c(res, N, c_mbit, rtt, b, "sync")
        d_ = fluido_n_c(res, N, c_mbit, rtt, b, "desync")
        qm, hq = ic_t(g.q_bdp.dropna()) if g.q_bdp.notna().sum() else (np.nan, np.nan)
        rm, hrm = ic_t(g.rho.dropna()) if g.rho.notna().sum() else (np.nan, np.nan)
        cm, hcm = ic_t(g.r.dropna()) if g.r.notna().sum() else (np.nan, np.nan)
        jm, hj = ic_t(g.jain)
        anc = anclas_sincronia(res, N, c_mbit, rtt, b)
        filas_t.append(dict(N=int(N), b=b, n=len(g), u=f(m, 2), h=f(h, 2), s=f(100 * s_["util"], 2),
                            d=f(100 * d_["util"], 2), ap=f(100 * modelos.appenzeller_util(N, b), 2),
                            qm=f(qm, 3), hq=f(hq, 3), qs=f(s_["q_bdp"], 3), qd=f(d_["q_bdp"], 3),
                            jm=f(jm, 4), hj=f(hj, 4), rho=f(rm, 2), hrho=f(hrm, 2), rs=f(np.sqrt(N), 2),
                            r=f(cm, 3), hr=f(hcm, 3),
                            pp=f"{int((g.p < 0.05).sum())}/{int(g.p.notna().sum())}",
                            dr=f(g.drops_s.mean(), 1),
                            rd=f(g.red_s.mean(), 1) if g.red_s.notna().any() else "—",
                            ed=f(d_["eventos_s"], 1), es=f(N * s_["eventos_s"], 1),
                            rhs=f(anc["sync"]["rho"], 2), rhd=f(anc["desync"]["rho"], 2),
                            rs_=f(anc["sync"]["r"], 2), rd_=f(anc["desync"]["r"], 2)))
    md.append("## Utilización y cola\n")
    md.append(tabla_md(filas_t, [("N", "N"), ("b", "b = B/BDP"), ("n", "n"), ("u", "Utilización medida (%)"),
                                  ("h", "± IC95"), ("s", "Fluido sincr. (%)"), ("d", "Fluido desincr. (%)"),
                                  ("ap", "Cota Appenzeller (%)"), ("qm", "Cola media / BDP"), ("hq", "± IC95"),
                                  ("qs", "Fluido sincr."), ("qd", "Fluido desincr."), ("dr", "Descartes tbf (por s)")]))

    md.append("## ¿Alcanza B = BDP/√N?\n")
    reglas = []
    for N in sorted(df.N.unique()):
        b_regla = 1 / np.sqrt(N)
        cand = df[df.N == N]
        b_cerca = min(cand.b.unique(), key=lambda x: abs(x - b_regla))
        g = cand[cand.b == b_cerca]
        m, h = ic_t(g.util)
        reglas.append(dict(N=int(N), br=f(b_regla, 3), bu=b_cerca, u=f(m, 2), h=f(h, 2),
                           ap=f(100 * modelos.appenzeller_util(N, b_cerca), 2),
                           s=f(100 * fluido_n_c(res, N, c_mbit, rtt, b_cerca, "sync")["util"], 2),
                           d=f(100 * fluido_n_c(res, N, c_mbit, rtt, b_cerca, "desync")["util"], 2),
                           ok95="sí" if m - (h if h == h else 0) >= 95 else "no",
                           ok99="sí" if m - (h if h == h else 0) >= 99 else "no"))
    md.append(tabla_md(reglas, [("N", "N"), ("br", "b = 1/√N"), ("bu", "b medido (más cercano)"),
                                 ("u", "Utilización medida (%)"), ("h", "± IC95"), ("ap", "Cota Appenzeller (%)"),
                                 ("s", "Fluido sincr. (%)"), ("d", "Fluido desincr. (%)"),
                                 ("ok95", "Límite inferior del IC ≥ 95 %"), ("ok99", "≥ 99 %")]))
    md.append("Si la utilización medida queda cerca del fluido *sincronizado* y lejos del desincronizado, la regla "
              "√N no se cumple porque los flujos se mueven juntos.\n")

    md.append("## ¿Se sincronizan los flujos?\n")
    md.append("Se mide con la ventana de cada flujo (muestreada cada 50 ms con `ss`). **ρ** = CV de la ventana total · "
              "√N / CV medio de un flujo: ≈ 1 si las fases son independientes, ≈ √N si están en fase y < 1 si los flujos "
              "se compensan entre sí (un flujo baja mientras otro sube). "
              "**r** = correlación media entre pares de flujos; su p sale de desplazar circularmente la serie de cada "
              "flujo un tiempo al azar (2000 veces), que conserva la forma de cada diente de sierra y destruye la fase "
              "relativa. La columna «reps con p < 0.05» cuenta en cuántas repeticiones r es mayor que lo esperable sin sincronía. "
              "Con 50 ms de muestreo y ciclos de ~0.2 s por flujo (N = 16) la resolución es gruesa: tomarlo como indicador. "
              "**Ojo:** ρ y r miden la correlación entre las *amplitudes* de las ventanas; pueden no ver que algunos "
              "flujos pierdan juntos mientras otros no. Por eso la sección siguiente agrega una prueba directa sobre los "
              "eventos de pérdida, y las conclusiones sobre sincronización tienen que apoyarse en ambas.\n")
    md.append(tabla_md(filas_t, [("N", "N"), ("b", "b"), ("rho", "ρ medido"), ("hrho", "± IC95"),
                                  ("rhs", "ρ fluido sincr."), ("rhd", "ρ fluido 1-por-pérdida"),
                                  ("r", "r medio"), ("hr", "± IC95"), ("rs_", "r fluido sincr."),
                                  ("rd_", "r fluido 1-por-pérdida"), ("pp", "Reps con p < 0.05"),
                                  ("jm", "Índice de Jain"), ("hj", "± IC95")]))
    filas_k = []
    for (N, b), g in sorted(df.groupby(["N", "b"])):
        cm, hcm2 = ic_t(g.coinc.dropna()) if g.coinc.notna().sum() else (np.nan, np.nan)
        kmed, kaz = g.k_med.mean(), g.k_azar.mean()
        valido = kaz == kaz and kaz < 0.9 * N
        um = g.util.mean()
        uk = 100 * fluido_n_c(res, N, c_mbit, rtt, b, "parcial", k=float(kmed))["util"] if valido else float("nan")
        filas_k.append(dict(N=int(N), b=b, coc=f(cm, 2), hc=f(hcm2, 2),
                            pp=f"{int((g.coinc_p < 0.05).sum())}/{int(g.coinc_p.notna().sum())}",
                            km=f(kmed, 2) if valido else "n/i", ka=f(kaz, 2), um=f(um, 2),
                            uk=f(uk, 2) if valido else "n/i", dk=f(uk - um, 2) if valido else "n/i",
                            us=f(100 * fluido_n_c(res, N, c_mbit, rtt, b, "sync")["util"], 2),
                            ud=f(100 * fluido_n_c(res, N, c_mbit, rtt, b, "desync")["util"], 2)))
    md.append("## Coincidencia de eventos de pérdida entre flujos\n")
    md.append("En cada flujo se detectan las reducciones bruscas de cwnd (más de 25 % entre dos muestras de 50 ms). "
              "**Cociente de coincidencias** = pares de eventos de flujos distintos separados a lo sumo 50 ms, observados "
              "sobre esperados al desplazar circularmente la serie de eventos de cada flujo un tiempo al azar (500 veces; "
              "conserva la frecuencia y la regularidad de cada flujo y destruye la fase relativa). ≈ 1 significa eventos "
              "independientes; p = P(nulo ≥ observado). **Flujos por episodio** = cuántos flujos distintos pierden en un "
              "mismo episodio (eventos encadenados con huecos de a lo sumo 100 ms). Solo es informativo si el valor «azar» "
              "queda bien por debajo de N: con muchos eventos por segundo (N grande) los episodios se encadenan por "
              "casualidad y el número satura (se marca n/i). **Fluido parcial**: cada pérdida reduce k flujos, con k = flujos "
              "por episodio medido; es una verificación cruzada, porque k sale de las ventanas por flujo y la utilización "
              "sale del goodput, que son mediciones independientes.\n")
    md.append(tabla_md(filas_k, [("N", "N"), ("b", "b"), ("coc", "Cociente de coincidencias (obs/azar)"), ("hc", "± IC95"),
                                  ("pp", "Reps con p < 0.05"), ("km", "Flujos por episodio, medido"),
                                  ("ka", "… esperado por azar"), ("um", "Utilización medida (%)"),
                                  ("uk", "Fluido parcial (k medido) (%)"), ("dk", "Diferencia (puntos)"),
                                  ("us", "Fluido sincr. (%)"), ("ud", "Fluido 1-por-pérdida (%)")]))
    md.append("## Frecuencia de reducciones de ventana\n")
    md.append("Suma sobre los N flujos de las reducciones bruscas de cwnd por segundo (más de 25 % entre dos muestras de "
              "50 ms; cuenta aproximada). En el fluido «un flujo por pérdida» cada evento reduce un flujo; en el sincronizado "
              "cada evento reduce N. Que las dos columnas del fluido den parecido es normal (la ventana que hay que "
              "descontar por segundo es la misma): este cuadro es una verificación de consistencia, no discrimina entre "
              "modelos. Quien discrimina es ρ y r.\n")
    md.append(tabla_md(filas_t, [("N", "N"), ("b", "b"), ("rd", "Reducciones medidas (por s)"),
                                  ("ed", "Fluido 1-por-pérdida (eventos por s)"),
                                  ("es", "Fluido sincr. (N × eventos por s)"), ("dr", "Descartes del tbf (por s)")]))
    falt = df[(df.flujos_sst.notna()) & (df.flujos_sst != df.N)]
    if len(falt):
        md.append(f"**AVISO:** en {len(falt)} corridas el muestreo vio un número de flujos distinto de N "
                  f"({sorted(set(int(v) for v in falt.flujos_sst))}); revisar `sst.jsonl`.\n")
    md.append("## ANOVA de dos factores (utilización: N × b)\n")
    df["bf"] = df.b.astype(str)
    df["Nf"] = "N" + df.N.astype(str)
    anova_dos_factores(df, "util", "Nf", "bf", md)
    md.append(seccion_cpu(corr))

    # ---- gráficos
    Ns = sorted(df.N.unique())
    fig, ax = plt.subplots(1, len(Ns), figsize=(5.8 * len(Ns), 4.4), squeeze=False)
    for k, N in enumerate(Ns):
        g0 = df[df.N == N]
        bs = sorted(g0.b.unique())
        bfino = np.logspace(np.log10(min(bs)), np.log10(max(bs)), 40)
        ax[0][k].plot(bs, [100 * fluido_n_c(res, N, c_mbit, rtt, b, "sync")["util"] for b in bs], "--", marker="x", label="fluido sincronizado")
        ax[0][k].plot(bs, [100 * fluido_n_c(res, N, c_mbit, rtt, b, "desync")["util"] for b in bs], "--", marker="x", label="fluido desincronizado")
        ax[0][k].plot(bfino, [100 * modelos.appenzeller_util(N, b) for b in bfino], ":", color="gray", label="cota Appenzeller")
        pts = [(b, *ic_t(g.util)) for b, g in g0.groupby("b")]
        bb, um, uh = map(np.array, zip(*pts))
        ax[0][k].errorbar(bb, um, yerr=np.nan_to_num(uh), fmt="ko", capsize=3, label="medido")
        ax[0][k].axvline(1 / np.sqrt(N), color="r", lw=.8, ls="-.", label="b = 1/√N")
        ax[0][k].set(xscale="log", xlabel="b = B/BDP", ylabel="Utilización (%)", title=f"N = {N} flujos Reno")
        ax[0][k].grid(alpha=.3, which="both")
        ax[0][k].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(res / exp / "buffer_n.png", dpi=130)
    plt.close(fig)

    claves = [(N, b, 1) for N in Ns for b in sorted(df.b.unique()) if (N, b, 1) in trazas]
    elegidas = [k for k in claves if abs(k[1] - 0.25) < 1e-9] or claves[:len(Ns)]
    if elegidas:
        fig, ax = plt.subplots(2, len(elegidas), figsize=(6 * len(elegidas), 6.2), squeeze=False, sharex=True)
        for k, (N, b, _) in enumerate(elegidas):
            t, M = trazas[(N, b, 1)]
            sel = t <= 10
            for j in range(M.shape[1]):
                ax[0][k].plot(t[sel], M[sel, j], lw=.7)
            ax[0][k].set(title=f"N = {N}, b = {b}: ventana de cada flujo (cwnd, seg.)")
            ax[1][k].plot(t[sel], M[sel].sum(axis=1), "k", lw=.9)
            ax[1][k].set(title="ventana total", xlabel="tiempo (s)")
            for r_ in (0, 1):
                ax[r_][k].grid(alpha=.3)
        fig.tight_layout()
        fig.savefig(res / exp / "sincronia.png", dpi=130)
        plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T4/T5: utilidades
BASE_RTT_MS = 0.33          # RTT base de la red emulada sin netem (T1)


def leer_contadores(path):
    """Contadores TCP del kernel a partir del texto crudo de /proc/net/netstat y /proc/net/snmp."""
    out = {}
    if not Path(path).exists():
        return out
    previo = None
    for linea in Path(path).read_text().splitlines():
        if linea.startswith("===") or ":" not in linea:
            previo = None
            continue
        pref, resto = linea.split(":", 1)
        campos = resto.split()
        if previo and previo[0] == pref and campos and campos[0].lstrip("-").isdigit():
            for k, v in zip(previo[1], campos):
                out[f"{pref}.{k}"] = int(v)
            previo = None
        else:
            previo = (pref, campos)
    return out


def leer_instante(path):
    """Epoch de la foto de contadores (línea que sigue a «=== t ===»), o None."""
    if not Path(path).exists():
        return None
    lineas = Path(path).read_text().splitlines()
    for i, l in enumerate(lineas[:-1]):
        if l.strip() == "=== t ===":
            try:
                return float(lineas[i + 1])
            except ValueError:
                return None
    return None


def delta_contadores(d):
    """Contadores TCP durante la ventana de medición: (después − foto al inicio de la ventana). Si esa foto no existe
    (corridas anteriores), cubre toda la corrida e incluye el arranque. Devuelve también dt_s y desde_ventana."""
    ini = "netstat_ventana.txt" if (d / "netstat_ventana.txt").exists() else "netstat_antes.txt"
    a_, b_ = leer_contadores(d / ini), leer_contadores(d / "netstat_despues.txt")
    if not a_ or not b_:
        return {}
    t0, t1 = leer_instante(d / ini), leer_instante(d / "netstat_despues.txt")
    claves = {"TcpExt.TCPTimeouts": "rto", "TcpExt.TCPLossProbes": "tlp", "TcpExt.TCPSackRecovery": "sackrec",
              "TcpExt.TCPRenoRecovery": "renorec", "TcpExt.TCPFastRetrans": "fastretr",
              "TcpExt.TCPSlowStartRetrans": "ssretr", "Tcp.RetransSegs": "retrans_segs", "Tcp.OutSegs": "out_segs"}
    out = {v: b_.get(k, 0) - a_.get(k, 0) for k, v in claves.items()}
    out["desde_ventana"] = ini == "netstat_ventana.txt"
    if t0 is not None and t1 is not None and t1 > t0:
        out["dt_s"] = t1 - t0
    return out


def pendiente_loglog(xs, ys):
    """Pendiente y su error estándar de log y contra log x (n ≥ 3)."""
    r = stats.linregress(np.log(np.asarray(xs, float)), np.log(np.asarray(ys, float)))
    return float(r.slope), float(r.stderr)


# ----------------------------------------------------------------------------- T4: pérdida aleatoria
def an_t4(res):
    exp = "t4-perdida"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas, diag = [], None
    for d, meta in corr:
        p = meta["params"]
        x = goodput_intervalos(d / "iperf.json")
        fila = dict(algo=p["algo"], rtt=p["rtt"], pct=p["loss"], rep=rep_de(meta), x=x.mean(), cap_mbit=p["C"])
        v = ventana_medicion(meta)
        if v:
            ss_df = leer_ss(d / "ss.jsonl", *v)
            m_ss = metricas_ss(ss_df)
            fila.update(m_ss)
            if not m_ss and diag is None:
                diag = f"{d.name}: {diagnostico_ss(d / 'ss.jsonl')}"
        cnt = delta_contadores(d)
        fila.update({"c_" + k: val for k, val in cnt.items() if k not in ("dt_s", "desde_ventana")})
        fila["t_cnt"] = cnt.get("dt_s") or (p["dur"] + p["omit"])
        fila["c_ventana"] = bool(cnt.get("desde_ventana", False))
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("srtt", "rto_ms", "cwnd_mean", "c_rto", "c_sackrec", "c_renorec", "c_tlp", "c_retrans_segs", "c_out_segs",
                "c_ventana"):
        if col not in df:
            df[col] = np.nan
    c_mbit = float(df.cap_mbit.iloc[0])
    cap = ETA * c_mbit
    md = ["# T4 — Pérdida aleatoria: goodput vs p\n"]
    if diag:
        md.append(f"**AVISO:** no se pudo leer el socket (`ss`) en alguna corrida. {diag}\n")
    md.append(f"C = {c_mbit:.0f} Mbit/s (η·C = {cap:.2f}), buffer = BDP, pérdida de netem solo en el sentido de los datos. "
              "IC95 t entre repeticiones. Las corridas duran 30 s (RTT 20 ms) y 60 s (RTT 100 ms).\n")
    md.append("**Modelos** (goodput en Mbit/s, todos topados en η·C; p = fracción de paquetes perdidos): "
              "*Mathis* x = (MSS/RTT)·√(3/(2p)), pendiente −1/2. *PFTK* agrega timeouts: "
              "1/(RTT√(2bp/3) + T₀·min(1, 3√(3bp/8))·p·(1+32p²)) con b = 1 (Linux cuenta los segmentos reconocidos) y "
              "T₀ = RTO medido con `ss`; pendiente −1/2 a p chico y hacia −3/2 cuando dominan los timeouts. "
              "*CUBIC (RFC 9438)*: máximo entre la región cúbica, W = [C(4−β_d)/(4β_d)]^¼·(RTT/p)^¾ (pendiente −3/4), "
              "y la región amigable con Reno (= Mathis); cruzan hacia p ≈ RTT³/1.82 (≈ 0.055 % a 100 ms, "
              "≈ 0.0004 % a 20 ms). *BBR*: x ≈ η·C·(1 − p). El RTT de los modelos es el srtt medido con `ss`. "
              "Las fórmulas se validaron contra una simulación de pérdida periódica (`python modelos.py`).\n")
    md.append("**Cautela con CUBIC a RTT 100 ms y p = 0.01 %:** en región cúbica la ventana máxima converge a su valor "
              "de régimen muy lentamente (la simulación necesita cientos de ciclos y una corrida de 60 s tiene ~12), así "
              "que ahí se mide un transitorio y el modelo de régimen tiende a quedar por debajo.\n")

    cond = []
    for (algo, rtt, pct), g in df.groupby(["algo", "rtt", "pct"]):
        m, h = ic_t(g.x)
        srtt = g.srtt.mean() if g.srtt.notna().any() else rtt + BASE_RTT_MS
        t0 = g.rto_ms.mean() / 1e3 if g.rto_ms.notna().any() else 0.2
        pf = pct / 100
        mod = dict(mathis=modelos.mathis_x(pf, srtt / 1e3, c_mbit), pftk=modelos.pftk_x(pf, srtt / 1e3, t0, 1.0, c_mbit),
                   cubic=modelos.cubic_x(pf, srtt / 1e3, c_mbit), bbr=modelos.bbr_x(pf, c_mbit))
        fila = dict(algo=algo, rtt=rtt, pct=pct, n=len(g), x=m, h=h, srtt=srtt, t0=t0, cwnd=g.cwnd_mean.mean())
        for k, v_ in mod.items():
            fila[f"m_{k}"] = v_
            fila[f"e_{k}"] = 100 * (m - v_) / v_
        _, p_t = (None, float("nan"))
        fila["saturado"] = m >= 0.9 * cap
        r_, o_ = g.c_retrans_segs.sum(), g.c_out_segs.sum()
        fila["retr_pct"] = 100 * r_ / o_ if o_ and o_ == o_ else float("nan")
        den = g.c_rto.sum() + g.c_sackrec.sum() + g.c_renorec.sum()
        fila["rto_s"] = g.c_rto.mean() / g.t_cnt.mean() if g.c_rto.notna().any() else float("nan")
        fila["rec_s"] = (g.c_sackrec.mean() + g.c_renorec.mean()) / g.t_cnt.mean() if g.c_sackrec.notna().any() else float("nan")
        fila["tlp_s"] = g.c_tlp.mean() / g.t_cnt.mean() if g.c_tlp.notna().any() else float("nan")
        fila["rto_share"] = g.c_rto.sum() / den if (den and den == den) else float("nan")
        cond.append(fila)
    cdf = pd.DataFrame(cond).sort_values(["algo", "rtt", "pct"])

    md.append("## Goodput medido y modelos\n")
    md.append(tabla_md([dict(a=r.algo, rtt=int(r.rtt), p=r.pct, n=int(r.n), x=f(r.x, 3), h=f(r.h, 3), sr=f(r.srtt, 2),
                             ma=f(r.m_mathis, 3), pf=f(r.m_pftk, 3), cu=f(r.m_cubic, 3), bb=f(r.m_bbr, 2))
                        for r in cdf.itertuples()],
                       [("a", "Algoritmo"), ("rtt", "RTT (ms)"), ("p", "p (%)"), ("n", "n"), ("x", "Goodput (Mbit/s)"),
                        ("h", "± IC95"), ("sr", "srtt (ms)"), ("ma", "Mathis"), ("pf", "PFTK"), ("cu", "CUBIC RFC 9438"),
                        ("bb", "BBR η·C(1−p)")]))
    md.append("## Error de cada modelo (%, (medido − modelo)/modelo)\n")
    md.append("Negativo = el modelo sobreestima. «sat.» marca los puntos donde el enlace es el límite (≥ 90 % de η·C): "
              "ahí ningún modelo de pérdida aplica y esos puntos no entran en los resúmenes de más abajo. Para BBR la saturación es su "
              "régimen normal: su modelo, η·C(1−p), se evalúa con todos sus puntos.\n")
    md.append(tabla_md([dict(a=r.algo, rtt=int(r.rtt), p=r.pct, ma=f(r.e_mathis, 1), pf=f(r.e_pftk, 1), cu=f(r.e_cubic, 1),
                             bb=f(r.e_bbr, 1), sat="sat." if r.saturado else "") for r in cdf.itertuples()],
                       [("a", "Algoritmo"), ("rtt", "RTT (ms)"), ("p", "p (%)"), ("ma", "Mathis"), ("pf", "PFTK"),
                        ("cu", "CUBIC"), ("bb", "BBR"), ("sat", "")]))

    md.append("## ¿Qué modelo explica mejor a cada algoritmo?\n")
    md.append("Sobre los puntos limitados por la pérdida: **RMS** de ln(medido/modelo) (0 = perfecto; 0.1 ≈ 10 %) y "
              "**sesgo** = media geométrica de medido/modelo − 1.\n")
    filas_r = []
    for (algo, rtt), g_all in cdf.groupby(["algo", "rtt"]):
        for k, nom in (("mathis", "Mathis"), ("pftk", "PFTK"), ("cubic", "CUBIC"), ("bbr", "BBR")):
            # los modelos de pérdida no aplican donde manda el enlace; el modelo de BBR sí aplica a BBR en todo el rango
            g = g_all if (k == "bbr" and algo == "bbr") else g_all[~g_all.saturado]
            if len(g):
                lr = np.log(g.x / g[f"m_{k}"])
                filas_r.append(dict(a=algo, rtt=int(rtt), mo=nom, n=len(g), rms=f(float(np.sqrt((lr ** 2).mean())), 3),
                                    sesgo=f(100 * (np.exp(lr.mean()) - 1), 1)))
    md.append(tabla_md(filas_r, [("a", "Algoritmo"), ("rtt", "RTT (ms)"), ("mo", "Modelo"), ("n", "Puntos"),
                                  ("rms", "RMS de ln(medido/modelo)"), ("sesgo", "Sesgo (%)")]))

    md.append("## Pendiente de log x contra log p (solo puntos no saturados)\n")
    md.append("Unidad de ajuste: la media de cada condición. IC95 t con n−2 gl; el p es de H₀: pendiente = −1/2 "
              "(Mathis). Las pendientes de los modelos se calculan sobre los mismos valores de p.\n")
    filas_s = []
    for (algo, rtt), g in cdf.groupby(["algo", "rtt"]):
        g = g if algo == "bbr" else g[~g.saturado]       # BBR vive cerca de η·C: se usan todos sus puntos
        if len(g) < 3:
            continue
        s, se = pendiente_loglog(g.pct, g.x)
        h = stats.t.ppf(0.975, len(g) - 2) * se
        p0 = 2 * stats.t.sf(abs((s + 0.5) / se), len(g) - 2)
        ms = {k: pendiente_loglog(g.pct, g[f"m_{k}"])[0] for k in ("mathis", "pftk", "cubic", "bbr")}
        filas_s.append(dict(a=algo, rtt=int(rtt), n=len(g), s=f(s, 3), h=f(h, 3), p0=pval(p0), ma=f(ms["mathis"], 3),
                            pf=f(ms["pftk"], 3), cu=f(ms["cubic"], 3), bb=f(ms["bbr"], 3)))
    md.append(tabla_md(filas_s, [("a", "Algoritmo"), ("rtt", "RTT (ms)"), ("n", "Puntos"), ("s", "Pendiente medida"),
                                  ("h", "± IC95"), ("p0", "p (H₀: −0.5)"), ("ma", "Mathis"), ("pf", "PFTK"),
                                  ("cu", "CUBIC"), ("bb", "BBR")]))

    md.append("## ¿Por qué un modelo más completo puede predecir peor? Timeouts reales vs los de PFTK\n")
    if not df.c_ventana.fillna(False).astype(bool).all():
        md.append("**AVISO:** hay corridas sin la foto de contadores al inicio de la ventana de medición: sus contadores "
                  "cubren toda la corrida e **incluyen el arranque**, donde Reno (sin HyStart) puede desbordar el buffer en "
                  "slow start y sumar retransmisiones y recuperaciones que no son del régimen estacionario.\n")
    md.append("Contadores TCP del kernel (por segundo, desde el inicio de la ventana de medición hasta el final de la corrida): **RTO** = timeouts de "
              "retransmisión; **recuperaciones** = entradas a recuperación rápida (SACK o Reno); **TLP** = sondas de "
              "pérdida de cola (RACK-TLP, que evitan muchos timeouts). «Fracción de RTO» = RTO / (RTO + recuperaciones). "
              "PFTK supone que la fracción de pérdidas que terminan en timeout es Q ≈ min(1, 3/w̄), con w̄ la ventana "
              "media. «retrans / segmentos» debe parecerse a p: es una verificación cruzada de la pérdida emulada.\n")
    filas_n = []
    for r in cdf[cdf.algo != "bbr"].itertuples():
        w = r.cwnd
        filas_n.append(dict(a=r.algo, rtt=int(r.rtt), p=r.pct, rto=f(r.rto_s, 2), rec=f(r.rec_s, 2), tlp=f(r.tlp_s, 2),
                            sh=f(100 * r.rto_share, 1) if r.rto_share == r.rto_share else "—",
                            w=f(w, 1), q=f(100 * min(1.0, 3 / w), 1) if (w == w and w > 0) else "—",
                            rp=f(r.retr_pct, 3)))
    md.append(tabla_md(filas_n, [("a", "Algoritmo"), ("rtt", "RTT (ms)"), ("p", "p (%)"), ("rto", "RTO por s"),
                                  ("rec", "Recuperaciones por s"), ("tlp", "TLP por s"), ("sh", "Fracción de RTO medida (%)"),
                                  ("w", "Ventana media (seg.)"), ("q", "Q de PFTK = min(1, 3/w̄) (%)"),
                                  ("rp", "retrans / segmentos (%)")]))

    md.append("## ANOVA de dos factores sobre ln(goodput): algoritmo × p, por RTT\n")
    for rtt in sorted(df.rtt.unique()):
        dr = df[df.rtt == rtt].copy()
        dr["lnx"] = np.log(dr.x)
        dr["pf"] = dr.pct.astype(str)
        md.append(f"**RTT {int(rtt)} ms**\n")
        anova_dos_factores(dr, "lnx", "algo", "pf", md)
    md.append(seccion_cpu(corr))

    algos, rtts = sorted(df.algo.unique()), sorted(df.rtt.unique())
    fig, ax = plt.subplots(len(rtts), len(algos), figsize=(4.9 * len(algos), 4.2 * len(rtts)), squeeze=False)
    for i, rtt in enumerate(rtts):
        for j, algo in enumerate(algos):
            sub = cdf[(cdf.algo == algo) & (cdf.rtt == rtt)].sort_values("pct")
            ax_ = ax[i][j]
            ax_.errorbar(sub.pct, sub.x, yerr=np.nan_to_num(sub.h), fmt="ko", capsize=3, label="medido", zorder=5)
            pg = np.logspace(np.log10(sub.pct.min()), np.log10(sub.pct.max()), 40)
            sr, t0 = sub.srtt.mean() / 1e3, sub.t0.mean()
            ax_.plot(pg, [modelos.mathis_x(q / 100, sr, c_mbit) for q in pg], label="Mathis")
            ax_.plot(pg, [modelos.pftk_x(q / 100, sr, t0, 1.0, c_mbit) for q in pg], label="PFTK")
            ax_.plot(pg, [modelos.cubic_x(q / 100, sr, c_mbit) for q in pg], "--", label="CUBIC RFC 9438")
            ax_.plot(pg, [modelos.bbr_x(q / 100, c_mbit) for q in pg], ":", label="BBR η·C(1−p)")
            ax_.set(xscale="log", yscale="log", xlabel="p (%)", ylabel="Goodput (Mbit/s)", title=f"{algo}, RTT {int(rtt)} ms")
            ax_.grid(alpha=.3, which="both")
            ax_.legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(res / exp / "perdida.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- T5: equidad
def goodput_flujo(path):
    return float(goodput_intervalos(path).mean())


def an_t5_n(res):
    """(a) N flujos del mismo algoritmo: índice de Jain."""
    exp = "t5-n"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas = []
    for d, meta in corr:
        p = meta["params"]
        c_mbit = p["C"]
        bdp = modelos.bdp_bytes(c_mbit, p["rtt"])
        por = goodput_por_flujo(d / "iperf.json")
        x = goodput_intervalos(d / "iperf.json")
        fila = dict(algo=p["algo"], N=p["flows"], rep=rep_de(meta), util=100 * x.mean() / (ETA * c_mbit), jain=jain(por),
                    max_min=float(por.max() / por.min()), max_rel=float(por.max() / por.mean()),
                    min_rel=float(por.min() / por.mean()))
        fila.update(ventana_cola(d, meta, bdp)[0])
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("q_bdp", "drops_s"):
        if col not in df:
            df[col] = np.nan
    md = ["# T5(a) — N flujos del mismo algoritmo: índice de Jain\n"]
    md.append("RTT = 20 ms, buffer = BDP, `iperf3 -P N` (todos los flujos con el mismo algoritmo y RTT). "
              "**Jain** J = (Σxᵢ)²/(n·Σxᵢ²) ∈ [1/n, 1]: 1 = reparto exacto. Cada corrida usa el goodput medio de cada flujo "
              "(sin el arranque); IC95 t entre repeticiones.\n")
    filas_t = []
    for (algo, N), g in sorted(df.groupby(["algo", "N"])):
        jm, jh = ic_t(g.jain)
        um, uh = ic_t(g.util)
        filas_t.append(dict(a=algo, N=int(N), n=len(g), j=f(jm, 4), jh=f(jh, 4), mn=f(1 / N, 3), mi=f(g.min_rel.mean(), 2),
                            ma=f(g.max_rel.mean(), 2), mm=f(g.max_min.mean(), 2), u=f(um, 2), uh=f(uh, 2),
                            q=f(g.q_bdp.mean(), 3), dr=f(g.drops_s.mean(), 1)))
    md.append(tabla_md(filas_t, [("a", "Algoritmo"), ("N", "N"), ("n", "n"), ("j", "Jain"), ("jh", "± IC95"),
                                  ("mn", "Mínimo posible 1/N"), ("mi", "Flujo menor / media"), ("ma", "Flujo mayor / media"),
                                  ("mm", "Mayor / menor"), ("u", "Utilización (%)"), ("uh", "± IC95"),
                                  ("q", "Cola media / BDP"), ("dr", "Descartes tbf (por s)")]))
    md.append("## ANOVA de dos factores sobre el índice de Jain: algoritmo × N\n")
    df["Nf"] = "N" + df.N.astype(str)
    anova_dos_factores(df, "jain", "algo", "Nf", md)
    md.append(seccion_cpu(corr))
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for algo, g0 in df.groupby("algo"):
        pts = [(N, *ic_t(g.jain)) for N, g in g0.groupby("N")]
        Ns, jm, jh = map(np.array, zip(*pts))
        ax[0].errorbar(Ns, jm, yerr=np.nan_to_num(jh), marker="o", capsize=3, label=algo)
        ax[1].plot(sorted(g0.N.unique()), [g.max_min.mean() for _, g in sorted(g0.groupby("N"))], marker="o", label=algo)
    ax[0].set(xlabel="N flujos", ylabel="Índice de Jain", title="Equidad entre flujos iguales", ylim=(0, 1.02))
    ax[1].set(xlabel="N flujos", ylabel="goodput mayor / menor", title="Dispersión entre flujos", yscale="log")
    for x_ in ax:
        x_.grid(alpha=.3, which="both")
        x_.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(res / exp / "jain.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


def an_t5_rtt(res):
    """(b) Dos flujos con RTT distinto: exponente a de x1/x2 = (RTT2/RTT1)^a."""
    exp = "t5-rtt"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas = []
    for d, meta in corr:
        p = meta["params"]
        rtts = p["rtts"]
        c_mbit = p["C"]
        xs = [goodput_flujo(d / "iperf_1.json"), goodput_flujo(d / "iperf_2.json")]
        i_c = 0 if rtts[0] == rtts[1] else int(np.argmin(rtts))        # flujo «corto» (en el control: el del puerto 1)
        i_l = 1 - i_c
        v = ventana_medicion(meta)
        sr = [metricas_ss(leer_ss(d / f"ss{i + 1}.jsonl", *v)).get("srtt", np.nan) if v else np.nan for i in (0, 1)]
        bdp = modelos.bdp_bytes(c_mbit, p["rtt"])
        fila = dict(algo=p["algos"][0], r2=max(rtts), rep=rep_de(meta), ratio=xs[i_c] / xs[i_l],
                    lrt=float(np.log(max(rtts) / min(rtts))), x_c=xs[i_c], x_l=xs[i_l], util=100 * sum(xs) / (ETA * c_mbit),
                    jain=jain(xs), srtt_c=sr[i_c], srtt_l=sr[i_l])
        fila["lr"] = float(np.log(fila["ratio"]))
        fila.update(ventana_cola(d, meta, bdp)[0])
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("q_bdp", "drops_s"):
        if col not in df:
            df[col] = np.nan
    md = ["# T5(b) — Dos flujos con RTT distinto: exponente de sesgo por RTT\n"]
    md.append("Un flujo con RTT = 20 ms y otro con RTT₂ (20 ms = **control**: debe repartir parejo, y valida el armado con "
              "`tc5.sh`). Dos clientes `iperf3` en puertos distintos; en las repeticiones pares se intercambian los puertos. "
              "Buffer = BDP de 20 ms. Razón x₁/x₂ = goodput del flujo corto / goodput del largo, y se ajusta "
              "ln(x₁/x₂) = a·ln(RTT₂/RTT₁) + c con todas las corridas (el control aporta puntos en ln = 0).\n")
    md.append("**Teoría.** Reno: con pérdidas sincronizadas, Mathis da x₁/x₂ = RTT₂/RTT₁, o sea **a = 1**. CUBIC: fuera de la "
              "región amigable con Reno su ventana crece en tiempo real y no en RTTs, así que dos flujos con distinto RTT "
              "tienden a ventanas parecidas y un cociente de goodput lineal en el RTT (también a = 1; ver el principio de "
              "diseño en el RFC 9438). Con pérdida aleatoria de tasa fija, en cambio, la respuesta de CUBIC da x ∝ RTT^(−1/4) "
              "(a = 1/4, T4); aquí el desborde del buffer es compartido, no aleatorio. BBR no tiene fórmula cerrada simple "
              "(ver Hock et al. 2017 y Ware et al. 2019 de la bibliografía): se reporta lo medido.\n")
    filas_c = []
    for (algo, r2), g in sorted(df.groupby(["algo", "r2"])):
        rm, rh = ic_t(g.ratio)
        if r2 != 20:
            ex = np.log(g.ratio) / g.lrt
            em, eh = ic_t(ex)
        else:
            em, eh = float("nan"), float("nan")
        um, uh = ic_t(g.util)
        filas_c.append(dict(a=algo, r2=int(r2), n=len(g), r=f(rm, 3), rh=f(rh, 3), t1=f(r2 / 20, 2), t4=f((r2 / 20) ** .25, 2),
                            e=f(em, 3), eh=f(eh, 3), j=f(g.jain.mean(), 4), u=f(um, 2), uh=f(uh, 2),
                            q=f(g.q_bdp.mean(), 3),
                            s1=f(g.srtt_c.mean(), 2), s2=f(g.srtt_l.mean(), 2)))
    md.append("## Razón de goodput por condición\n")
    md.append(tabla_md(filas_c, [("a", "Algoritmo"), ("r2", "RTT₂ (ms)"), ("n", "n"), ("r", "x₁/x₂ medida"), ("rh", "± IC95"),
                                  ("t1", "Si a = 1"), ("t4", "Si a = 1/4"), ("e", "Exponente por condición"),
                                  ("eh", "± IC95"), ("j", "Jain"), ("u", "Utilización (%)"), ("uh", "± IC95"),
                                  ("q", "Cola media / BDP")]))
    md.append("## Verificación del RTT por flujo (srtt del emisor, ss)\n")
    md.append("El srtt de cada flujo incluye la cola del cuello de botella, que es **común** a los dos. Por eso se comparan dos "
              "cosas: la *diferencia* de srtt entre el flujo largo y el corto, que debe coincidir con RTT₂ − RTT₁, y la *cola* de "
              "cada flujo (srtt − RTT configurado − 0.33 ms de base), que debe ser parecida entre los dos. Si el filtro u32 no "
              "separara bien los flujos, la diferencia no coincidiría.\n")
    filas_v = []
    for (algo, r2), g in sorted(df.groupby(["algo", "r2"])):
        s1, s2 = g.srtt_c.mean(), g.srtt_l.mean()
        filas_v.append(dict(a=algo, r2=int(r2), c=f(s1, 2), l=f(s2, 2), dif=f(s2 - s1, 2), cfg=f(r2 - 20, 1),
                            q1=f(s1 - 20.33, 2), q2=f(s2 - (r2 + 0.33), 2)))
    md.append(tabla_md(filas_v, [("a", "Algoritmo"), ("r2", "RTT₂ (ms)"), ("c", "srtt flujo corto (ms)"),
                                  ("l", "srtt flujo largo (ms)"), ("dif", "srtt largo − corto (ms)"),
                                  ("cfg", "RTT₂ − RTT₁ configurado (ms)"), ("q1", "Cola del flujo corto (ms)"),
                                  ("q2", "Cola del flujo largo (ms)")]))
    md.append("## Exponente a por regresión\n")
    filas_a = []
    for algo, g in sorted(df.groupby("algo")):
        if g.lrt.nunique() < 2 or len(g) < 4:
            continue
        r = stats.linregress(g.lrt, g.lr)
        t = stats.t.ppf(0.975, len(g) - 2)
        p1 = 2 * stats.t.sf(abs((r.slope - 1) / r.stderr), len(g) - 2)
        p4 = 2 * stats.t.sf(abs((r.slope - 0.25) / r.stderr), len(g) - 2)
        pc = 2 * stats.t.sf(abs(r.intercept / r.intercept_stderr), len(g) - 2)
        filas_a.append(dict(a=algo, n=len(g), s=f(r.slope, 3), h=f(t * r.stderr, 3), p1=pval(p1), p4=pval(p4),
                            c=f(r.intercept, 3), ch=f(t * r.intercept_stderr, 3), pc=pval(pc), r2=f(r.rvalue ** 2, 3)))
    md.append(tabla_md(filas_a, [("a", "Algoritmo"), ("n", "Corridas"), ("s", "Exponente a"), ("h", "± IC95"),
                                  ("p1", "p (H₀: a = 1)"), ("p4", "p (H₀: a = 1/4)"), ("c", "Intercepto c"), ("ch", "± IC95"),
                                  ("pc", "p (H₀: c = 0)"), ("r2", "R²")]))
    md.append("a > 0: gana el flujo de RTT corto; a < 0: gana el de RTT largo. El intercepto c ≠ 0 indicaría un sesgo que no es "
              "del RTT (puerto, orden de arranque). Un p chico no implica un desvío importante: mirar a, su IC y el control.\n")
    md.append(seccion_cpu(corr))
    fig, ax = plt.subplots(figsize=(6.2, 4.6))
    xs_ = np.linspace(0, df.lrt.max() * 1.05, 20)
    for algo, g in df.groupby("algo"):
        ax.scatter(g.lrt, g.lr, s=22, label=f"{algo}")
        if g.lrt.nunique() >= 2 and len(g) >= 4:
            r = stats.linregress(g.lrt, g.lr)
            ax.plot(xs_, r.intercept + r.slope * xs_, lw=.9)
    ax.plot(xs_, xs_, "k--", lw=.8, label="a = 1")
    ax.plot(xs_, .25 * xs_, "k:", lw=.8, label="a = 1/4")
    ax.set(xlabel="ln(RTT₂/RTT₁)", ylabel="ln(x₁/x₂)", title="Sesgo por RTT")
    ax.grid(alpha=.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(res / exp / "sesgo_rtt.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


def an_t5_algos(res):
    """(c) Dos algoritmos distintos compiten: ¿quién gana y cómo depende del buffer?"""
    exp = "t5-algos"
    corr = corridas(res, exp)
    if not corr:
        return None
    filas = []
    for d, meta in corr:
        p = meta["params"]
        c_mbit = p["C"]
        A, B = meta["serie"].split("_vs_")
        xs = dict(zip(p["algos"], [goodput_flujo(d / "iperf_1.json"), goodput_flujo(d / "iperf_2.json")]))
        bdp = modelos.bdp_bytes(c_mbit, p["rtt"])
        fila = dict(par=meta["serie"], A=A, B=B, b=p["b"], buf=p["buf"], rep=rep_de(meta), xA=xs[A], xB=xs[B],
                    share=xs[A] / (xs[A] + xs[B]), jain=jain([xs[A], xs[B]]), util=100 * (xs[A] + xs[B]) / (ETA * c_mbit))
        fila.update(ventana_cola(d, meta, bdp)[0])
        filas.append(fila)
    df = pd.DataFrame(filas)
    for col in ("q_bdp", "drops_s", "perd"):
        if col not in df:
            df[col] = np.nan
    md = ["# T5(c) — Dos algoritmos distintos compiten\n"]
    md.append("RTT = 20 ms para ambos, buffer b = B/BDP. «Parte de A» = goodput de A / (goodput de A + goodput de B) para el "
              "primer algoritmo del par; 0.5 = reparto parejo. En las repeticiones pares se intercambian los puertos. "
              "p = prueba t de una muestra contra 0.5 entre repeticiones (con solo 3 repeticiones el IC es ancho: mirar la "
              "diferencia, no solo el p). No hay fórmula cerrada simple para estos pares; la bibliografía discute BBR contra "
              "algoritmos basados en pérdida (Hock et al. 2017; Ware et al. 2019).\n")
    filas_t = []
    for (par, b), g in sorted(df.groupby(["par", "b"])):
        A, B = g.A.iloc[0], g.B.iloc[0]
        sm, sh = ic_t(g.share)
        pv = stats.ttest_1samp(g.share, 0.5).pvalue if len(g) >= 2 else float("nan")
        um, uh = ic_t(g.util)
        gana = A if sm > 0.5 else B
        filas_t.append(dict(par=f"{A} vs {B}", b=b, B_=f(g.buf.iloc[0] / 1000, 1), n=len(g), s=f(100 * sm, 1), sh=f(100 * sh, 1),
                            p=pval(pv), gana=gana, j=f(g.jain.mean(), 3), u=f(um, 2), uh=f(uh, 2), q=f(g.q_bdp.mean(), 3),
                            pe=f(g.perd.mean(), 3), xa=f(g.xA.mean(), 1), xb=f(g.xB.mean(), 1)))
    md.append(tabla_md(filas_t, [("par", "Par (A vs B)"), ("b", "b = B/BDP"), ("B_", "B (kB)"), ("n", "n"),
                                  ("xa", "Goodput A (Mbit/s)"), ("xb", "Goodput B (Mbit/s)"), ("s", "Parte de A (%)"),
                                  ("sh", "± IC95"), ("p", "p (t, vs 50 %)"), ("gana", "Mayor parte"), ("j", "Jain"),
                                  ("u", "Utilización (%)"), ("uh", "± IC95"), ("q", "Cola media / BDP"),
                                  ("pe", "Pérdida en el tbf (%)")]))
    md.append("## ANOVA de dos factores sobre la parte de A: par × b\n")
    df["bf"] = df.b.astype(str)
    anova_dos_factores(df, "share", "par", "bf", md)
    md.append(seccion_cpu(corr))
    pares = sorted(df.par.unique())
    bs = sorted(df.b.unique())
    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.4))
    ancho = 0.8 / len(pares)
    for k, par in enumerate(pares):
        g0 = df[df.par == par]
        pts = [ic_t(g0[g0.b == b].share) for b in bs]
        pos = np.arange(len(bs)) + k * ancho
        ax[0].bar(pos, [100 * m for m, _ in pts], ancho * .9, yerr=[100 * np.nan_to_num(h) for _, h in pts], capsize=3,
                  label=par.replace("_vs_", " vs "))
        ax[1].bar(pos, [g0[g0.b == b].q_bdp.mean() for b in bs], ancho * .9, label=par.replace("_vs_", " vs "))
    for x_ in ax:
        x_.set_xticks(np.arange(len(bs)) + ancho * (len(pares) - 1) / 2)
        x_.set_xticklabels([str(b) for b in bs])
        x_.set_xlabel("b = B/BDP")
        x_.grid(alpha=.3, axis="y")
        x_.legend(fontsize=7)
    ax[0].axhline(50, color="k", ls="--", lw=.8)
    ax[0].set(ylabel="Parte del primer algoritmo (%)", title="Quién gana según el buffer")
    ax[1].set(ylabel="Cola media / BDP", title="Cola que forma la pareja")
    fig.tight_layout()
    fig.savefig(res / exp / "algoritmos.png", dpi=130)
    plt.close(fig)
    return "\n".join(md)


# ----------------------------------------------------------------------------- main
ANALISIS = {"t1-capacidad": an_capacidad, "t1-rtt": an_rtt, "t1-perdida": an_perdida,
            "t2-ventana": an_ventana, "t2-libre": an_libre, "t3-uno": an_t3_uno, "t3-n": an_t3_n,
            "t4-perdida": an_t4, "t5-n": an_t5_n, "t5-rtt": an_t5_rtt, "t5-algos": an_t5_algos}
GRUPOS = {"t1": ["t1-capacidad", "t1-rtt", "t1-perdida"], "t2": ["t2-ventana", "t2-libre"],
          "t3": ["t3-uno", "t3-n"], "t4": ["t4-perdida"], "t5": ["t5-n", "t5-rtt", "t5-algos"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exp", choices=list(ANALISIS) + list(GRUPOS))
    ap.add_argument("--results", default="results")
    a = ap.parse_args()
    res = Path(a.results)
    if not res.is_absolute():
        res = RAIZ / res
    for exp in GRUPOS.get(a.exp, [a.exp]):
        (res / exp).mkdir(parents=True, exist_ok=True)
        txt = ANALISIS[exp](res)
        if txt is None:
            print(f"{exp}: sin corridas con status ok en {res}")
            continue
        (res / exp / "resumen.md").write_text(txt)
        print(f"{exp}: escrito {res / exp / 'resumen.md'}")


if __name__ == "__main__":
    main()
