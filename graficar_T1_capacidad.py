import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

DIR_BASE = Path("results/T1-Capacidad")
resultados = {}

# 1. Leer y agrupar los datos crudos
for dir_corrida in DIR_BASE.iterdir():
    if not dir_corrida.is_dir(): continue
    
    meta_file = dir_corrida / "meta.json"
    iperf_file = dir_corrida / "iperf.json"
    
    if not meta_file.exists() or not iperf_file.exists():
        continue
        
    with open(meta_file) as f:
        meta = json.load(f)
    with open(iperf_file) as f:
        iperf = json.load(f)
        
    C = meta["parametros"]["capacidad_mbit"]
    alg = meta["parametros"]["algoritmo"]
    
    # Extraer el goodput (bits por segundo a Mbps)
    try:
        bps = iperf["end"]["sum_received"]["bits_per_second"]
    except KeyError:
        bps = iperf["end"]["sum_sent"]["bits_per_second"]
        
    mbps = bps / 1e6
    
    key = (C, alg)
    if key not in resultados:
        resultados[key] = []
    resultados[key].append(mbps)

# 2. Preparar el gráfico
capacidades_unicas = sorted(list(set(k[0] for k in resultados.keys())))
algoritmos = sorted(list(set(k[1] for k in resultados.keys())))

plt.figure(figsize=(10, 6))

# Línea Teórica ηC (1448 bytes de payload TCP / 1514 bytes de trama Ethernet)
eta = 1448 / 1514
C_array = np.array(capacidades_unicas)
plt.plot(C_array, C_array * eta, 'k--', label=f'Teórico ($\eta C, \eta={eta:.4f}$)', linewidth=2)

# Graficar los datos de cada algoritmo
colores = {'reno': 'blue', 'cubic': 'orange', 'bbr': 'green'}
marcadores = {'reno': 'o', 'cubic': 's', 'bbr': '^'}

for alg in algoritmos:
    x_vals = []
    y_vals = []
    y_err = []
    for C in capacidades_unicas:
        if (C, alg) in resultados:
            valores = resultados[(C, alg)]
            x_vals.append(C)
            y_vals.append(np.mean(valores))
            y_err.append(np.std(valores)) # Desviación estándar (Jitter de la medición)
            
    # Pequeño desfase visual en el eje X para que los puntos no se tapen entre sí
    offset = {'reno': -0.8, 'cubic': 0, 'bbr': 0.8}[alg]
    x_plot = [x + offset for x in x_vals]
    
    plt.errorbar(x_plot, y_vals, yerr=y_err, fmt=marcadores[alg], color=colores[alg], 
                 label=alg.upper(), capsize=5, linestyle='-', alpha=0.8)

# 3. Formateo y guardado
plt.title('T1: Calibración de Capacidad (Goodput vs Capacidad C)')
plt.xlabel('Capacidad Configurda $C$ en la red (Mbit/s)')
plt.ylabel('Goodput Medido en la capa de aplicación (Mbit/s)')
plt.grid(True, linestyle='--', alpha=0.6)
plt.xticks(capacidades_unicas)
plt.legend()
plt.tight_layout()

plt.savefig('T1_Capacidad_Grafico.png', dpi=300)
print("¡Gráfico generado exitosamente y guardado como 'T1_Capacidad_Grafico.png'!")
plt.show()
