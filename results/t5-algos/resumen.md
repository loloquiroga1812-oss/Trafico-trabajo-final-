# T5(c) — Dos algoritmos distintos compiten

RTT = 20 ms para ambos, buffer b = B/BDP. «Parte de A» = goodput de A / (goodput de A + goodput de B) para el primer algoritmo del par; 0.5 = reparto parejo. En las repeticiones pares se intercambian los puertos. p = prueba t de una muestra contra 0.5 entre repeticiones (con solo 3 repeticiones el IC es ancho: mirar la diferencia, no solo el p). No hay fórmula cerrada simple para estos pares; la bibliografía discute BBR contra algoritmos basados en pérdida (Hock et al. 2017; Ware et al. 2019).

| Par (A vs B) | b = B/BDP | B (kB) | n | Goodput A (Mbit/s) | Goodput B (Mbit/s) | Parte de A (%) | ± IC95 | p (t, vs 50 %) | Mayor parte | Jain | Utilización (%) | ± IC95 | Cola media / BDP | Pérdida en el tbf (%) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bbr vs cubic | 0.25 | 62.5 | 3 | 86.3 | 7.4 | 92.1 | 0.9 | <0.001 | bbr | 0.585 | 97.96 | 0.07 | 0.076 | 2.113 |
| bbr vs cubic | 1.0 | 250.0 | 3 | 84.1 | 10.1 | 89.3 | 2.1 | <0.001 | bbr | 0.618 | 98.49 | 0.10 | 0.726 | 0.460 |
| bbr vs cubic | 4.0 | 1000.0 | 3 | 40.8 | 54.7 | 42.7 | 5.0 | 0.024 | cubic | 0.978 | 99.89 | 0.15 | 2.570 | 0.126 |
| bbr vs reno | 0.25 | 62.5 | 3 | 87.7 | 5.9 | 93.7 | 0.9 | <0.001 | bbr | 0.567 | 97.84 | 0.10 | 0.081 | 2.320 |
| bbr vs reno | 1.0 | 250.0 | 3 | 85.1 | 9.1 | 90.4 | 0.8 | <0.001 | bbr | 0.605 | 98.46 | 0.09 | 0.708 | 0.759 |
| bbr vs reno | 4.0 | 1000.0 | 3 | 46.4 | 49.0 | 48.6 | 8.5 | 0.561 | reno | 0.996 | 99.72 | 0.31 | 2.290 | 0.040 |
| cubic vs reno | 0.25 | 62.5 | 3 | 46.3 | 45.2 | 50.6 | 3.1 | 0.469 | cubic | 0.999 | 95.70 | 1.65 | 0.086 | 0.036 |
| cubic vs reno | 1.0 | 250.0 | 3 | 51.1 | 44.5 | 53.5 | 9.4 | 0.253 | cubic | 0.991 | 99.99 | 0.08 | 0.688 | 0.012 |
| cubic vs reno | 4.0 | 1000.0 | 3 | 58.3 | 37.3 | 61.0 | 9.9 | 0.042 | cubic | 0.951 | 100.00 | 0.03 | 3.207 | 0.006 |

## ANOVA de dos factores sobre la parte de A: par × b

| Fuente | F | p |
|---|---|---|
| C(par) | 251.55 | <0.001 |
| C(bf) | 421.25 | <0.001 |
| C(par):C(bf) | 187.02 | <0.001 |
| Residual | — | — |

Supuestos: Shapiro-Wilk de residuos p = 0.006; Levene p = 0.816. Si alguno es < 0.05, el ANOVA no es confiable: Kruskal-Wallis entre «par» (grupos de 9 observaciones): p = 0.194.

### Chequeo de CPU

Governor del host: performance; no_turbo: [1]. 

Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:

| CPU | Mín | Media | Máx |
|---|---|---|---|
| 0 | 385 | 1156 | 2808 |
| 1 | 400 | 2738 | 2802 |
| 2 | 394 | 1731 | 2807 |
| 3 | 393 | 1063 | 2809 |

Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):

| Contenedor | Media % | Máx % |
|---|---|---|
| cli | 7.9 | 10.2 |
| r_bn | 4.5 | 5.4 |
| r_wan | 2.6 | 2.9 |
| srv | 2.9 | 3.2 |
