import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

DIR_BASE = Path("results/T1-Perdida")
resultados = {}

if not DIR_BASE.exists():
    print("No se encontró la carpeta results/T1-Perdida/. Ejecutá primero correr_T1_perdida.py")
    exit(1)

# 1. Procesar datos de las corridas
for d in DIR_BASE.iterdir():
    if not d.is_dir(): continue
    meta_file = d / "meta.json"
    traza_file = d / "traza.json"
    if not meta_file.exists() or not traza_file.exists(): continue
    
    with open(meta_file) as f: meta = json.load(f)
    with open(traza_file) as f: traza = json.load(f)
    
    p_cfg = meta["parametros"]["loss_configurado_pct"]
    total = traza["total_enviados"]
    recibidos = set(traza["recibidos"])
    
    # Reconstruir secuencia binaria: 0 = recibido, 1 = perdido
    secuencia_perdida = np.array([1 if i not in recibidos else 0 for i in range(total)])
    
    if p_cfg not in resultados:
        resultados[p_cfg] = {'secuencias': []}
    resultados[p_cfg]['secuencias'].append(secuencia_perdida)

# 2. Análisis estadístico por tasa de pérdida configurada
tasas_cfg = sorted(list(resultados.keys()))
tasas_medidas = []
ci_inferior = []
ci_superior = []
p_condicional_11 = []

print("\n--- REPORTE ESTADÍSTICO DE PÉRDIDAS ---")
for p_cfg in tasas_cfg:
    # Concatenar repeticiones para mayor robustez estadística
    todas_secuencias = np.concatenate(resultados[p_cfg]['secuencias'])
    N = len(todas_secuencias)
    perdidos_totales = np.sum(todas_secuencias)
    
    # Proporción empírica (en porcentaje)
    p_est = (perdidos_totales / N) * 100.0
    tasas_medidas.append(p_est)
    
    # Intervalo de confianza de la proporción (Wilson score interval al 95%, z = 1.96)
    p_frac = perdidos_totales / N
    z = 1.96
    denominador = 1 + (z**2 / N)
    centro = (p_frac + (z**2 / (2 * N))) / denominador
    margen = z * np.sqrt((p_frac * (1 - p_frac) / N) + (z**2 / (4 * N**2))) / denominador
    
    inf = max(0.0, (centro - margen) * 100.0)
    sup = min(100.0, (centro + margen) * 100.0)
    ci_inferior.append(p_est - inf)
    ci_superior.append(sup - p_est)
    
    # Test de ráfagas / independencia: Probabilidad condicional P(Loss | Loss previa) -> P(1|1)
    # Y[i] == 1 dado Y[i-1] == 1
    idx_perdidos = np.where(todas_secuencias == 1)[0]
    if len(idx_perdidos) > 1:
        # Filtrar para ver cuántos de los siguientes también se perdieron
        siguientes_perdidos = np.sum(todas_secuencias[idx_perdidos[:-1] + 1] == 1)
        p_11 = (siguientes_perdidos / (len(idx_perdidos) - 1)) * 100.0
    else:
        p_11 = 0.0
    p_condicional_11.append(p_11)
    
    print(f"Configurado: {p_cfg}% | Medido: {p_est:.3f}% [IC 95%: {inf:.3f}% - {sup:.3f}%] | P(1|1): {p_11:.3f}%")

# =====================================================================
# GENERACIÓN DE GRÁFICOS
# =====================================================================
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

# Gráfico 1: Pérdida Medida vs Configurada con Intervalos de Confianza
x_pos = np.arange(len(tasas_cfg))
ax1.errorbar(x_pos, tasas_medidas, yerr=[ci_inferior, ci_superior], fmt='o-', color='crimson', 
             capsize=5, linewidth=2, label='Medido ($\pm$ IC 95%)')
ax1.plot(x_pos, tasas_cfg, 'k--', label='Teórico Ideal ($p_{config} = p_{medido}$)', linewidth=1.5)

ax1.set_title('T1: Calibración de Tasa de Pérdida')
ax1.set_xlabel('Tasa de Pérdida Configurada (%)')
ax1.set_ylabel('Tasa de Pérdida Medida (%)')
ax1.set_xticks(x_pos)
ax1.set_xticklabels([f'{p}%' for p in tasas_cfg])
ax1.grid(True, linestyle='--', alpha=0.6)
ax1.legend()

# Gráfico 2: Test de Independencia vs Ráfagas (P(1|1) vs p_medido)
ax2.bar(x_pos - 0.2, tasas_medidas, width=0.4, label='Pérdida Media Global ($\hat{p}$)', color='steelblue')
ax2.bar(x_pos + 0.2, p_condicional_11, width=0.4, label='P(Perdido | Perdido Previo) [$P(1|1)$]', color='darkorange')

ax2.set_title('Test de Independencia vs. Ráfagas (Modelo Markoviano)')
ax2.set_xlabel('Tasa de Pérdida Configurada (%)')
ax2.set_ylabel('Probabilidad (%)')
ax2.set_xticks(x_pos)
ax2.set_xticklabels([f'{p}%' for p in tasas_cfg])
ax2.grid(True, linestyle='--', alpha=0.6)
ax2.legend()

plt.tight_layout()
plt.savefig('T1_Perdida_Analisis.png', dpi=300)
print("\n✔ Gráfico de análisis guardado como: 'T1_Perdida_Analisis.png'")
plt.show()
