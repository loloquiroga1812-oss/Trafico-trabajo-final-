import json
from pathlib import Path

for d in sorted(Path("results/H5").glob("K*_rho09_*")):
    m = json.loads((d / "meta.json").read_text())
    k = m["parametros"]["k_servidores"]
    cg, dur = m["cpu_cgroup_delta"], m["duracion_real_s"]
    rhos = [cg[f"api{i}"]["usage_usec"] / 1e6 / dur for i in range(1, k + 1)]
    lb = cg["lb"]["usage_usec"] / 1e6 / dur
    thr = {c: v["nr_throttled"] for c, v in cg.items()}
    print(d.name, [f"{r:.2f}" for r in rhos], f"lb={lb:.2f}", thr)
