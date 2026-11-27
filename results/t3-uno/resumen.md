# T3 — Un flujo: utilización y cola vs tamaño del buffer

C = 100 Mbit/s, RTT = 20 ms, BDP = C·RTT = 250 kB; b = B/BDP. «Utilización» = goodput / (η·C). Cada corrida aporta una media (la medición dura 60 s después de descartar el arranque); los IC95 son t de Student entre repeticiones.

**Modelos.** *Cerrado (Reno)*: U(b) = ¾(1+b)²/(1+b+b²) si b < 1 y U = 1 si b ≥ 1; cola media q/BDP = b²(3+2b)/(3(1+b+b²)) si b < 1 y 7(1+b)/9 − 1 si b ≥ 1 (deducción en `modelos.py`). *Fluido*: integra la ventana con RTT(t) = RTT₀ + cola/C y pérdida cuando la cola llega a B (Reno: β = 0.5; Cubic: β = 0.7, C = 0.4, región TCP-friendly, sin fast convergence). El fluido se validó contra las fórmulas cerradas antes de usarse (`python modelos.py`). BBR no tiene modelo.

## Utilización

| Algoritmo | b = B/BDP | B (kB) | n | Utilización medida (%) | ± IC95 | Cerrado Reno (%) | Fluido (%) | Error % vs fluido | p (t) |
|---|---|---|---|---|---|---|---|---|---|
| bbr | 0.05 | 12.5 | 3 | 96.79 | 0.46 | — | — | — | — |
| bbr | 0.1 | 25.0 | 3 | 96.79 | 0.51 | — | — | — | — |
| bbr | 0.25 | 62.5 | 3 | 97.57 | 0.11 | — | — | — | — |
| bbr | 0.5 | 125.0 | 3 | 97.79 | 0.03 | — | — | — | — |
| bbr | 1.0 | 250.0 | 3 | 97.78 | 0.00 | — | — | — | — |
| bbr | 2.0 | 500.0 | 3 | 97.78 | 0.00 | — | — | — | — |
| cubic | 0.05 | 12.5 | 3 | 20.67 | 5.80 | — | 89.11 | -76.8 | <0.001 |
| cubic | 0.1 | 25.0 | 3 | 71.80 | 11.50 | — | 92.50 | -22.4 | 0.016 |
| cubic | 0.25 | 62.5 | 3 | 97.18 | 0.03 | — | 98.48 | -1.3 | <0.001 |
| cubic | 0.5 | 125.0 | 3 | 99.98 | 0.03 | — | 100.00 | -0.0 | 0.092 |
| cubic | 1.0 | 250.0 | 3 | 100.00 | 0.03 | — | 100.00 | 0.0 | 0.945 |
| cubic | 2.0 | 500.0 | 3 | 99.99 | 0.00 | — | 100.00 | -0.0 | <0.001 |
| reno | 0.05 | 12.5 | 3 | 34.63 | 31.66 | 78.56 | 78.56 | -55.9 | 0.027 |
| reno | 0.1 | 25.0 | 3 | 71.67 | 7.36 | 81.76 | 81.65 | -12.2 | 0.028 |
| reno | 0.25 | 62.5 | 3 | 87.35 | 0.16 | 89.29 | 89.32 | -2.2 | <0.001 |
| reno | 0.5 | 125.0 | 3 | 95.13 | 0.07 | 96.43 | 96.39 | -1.3 | <0.001 |
| reno | 1.0 | 250.0 | 3 | 99.90 | 0.00 | 100.00 | 100.00 | -0.1 | <0.001 |
| reno | 2.0 | 500.0 | 3 | 100.01 | 0.03 | 100.00 | 100.00 | 0.0 | 0.403 |

El error % es lo que importa en la práctica: con varianzas tan chicas, casi cualquier diferencia sale «significativa». Un error negativo grande a b chico significa que el algoritmo real rinde menos que el diente de sierra ideal.

## Cola

| Algoritmo | b | Cola media / BDP medida | ± IC95 | Cerrado Reno | Fluido | Retardo de cola medio (ms) | srtt medio (ms) | Cola máx / BDP |
|---|---|---|---|---|---|---|---|---|
| bbr | 0.05 | 0.016 | 0.003 | — | — | 0.33 | 20.62 | 0.05 |
| bbr | 0.1 | 0.028 | 0.002 | — | — | 0.55 | 20.73 | 0.10 |
| bbr | 0.25 | 0.055 | 0.002 | — | — | 1.10 | 21.36 | 0.25 |
| bbr | 0.5 | 0.161 | 0.010 | — | — | 3.22 | 23.34 | 0.42 |
| bbr | 1.0 | 0.161 | 0.003 | — | — | 3.22 | 23.39 | 0.42 |
| bbr | 2.0 | 0.159 | 0.012 | — | — | 3.18 | 23.33 | 0.42 |
| cubic | 0.05 | 0.001 | 0.001 | — | 0.004 | 0.02 | 20.40 | 0.03 |
| cubic | 0.1 | 0.006 | 0.003 | — | 0.016 | 0.13 | 20.31 | 0.08 |
| cubic | 0.25 | 0.082 | 0.002 | — | 0.094 | 1.64 | 21.79 | 0.24 |
| cubic | 0.5 | 0.301 | 0.003 | — | 0.335 | 6.02 | 26.18 | 0.49 |
| cubic | 1.0 | 0.784 | 0.003 | — | 0.856 | 15.69 | 35.87 | 1.00 |
| cubic | 2.0 | 1.573 | 0.004 | — | 1.782 | 31.46 | 51.62 | 1.99 |
| reno | 0.05 | 0.002 | 0.001 | 0.002 | 0.002 | 0.03 | 20.34 | 0.04 |
| reno | 0.1 | 0.007 | 0.004 | 0.010 | 0.010 | 0.15 | 20.33 | 0.09 |
| reno | 0.25 | 0.050 | 0.000 | 0.056 | 0.056 | 1.01 | 21.21 | 0.24 |
| reno | 0.5 | 0.170 | 0.003 | 0.190 | 0.189 | 3.40 | 23.61 | 0.49 |
| reno | 1.0 | 0.529 | 0.002 | 0.556 | 0.552 | 10.58 | 30.73 | 0.99 |
| reno | 2.0 | 1.314 | 0.003 | 1.333 | 1.346 | 26.29 | 46.43 | 1.99 |

## Eventos de pérdida: ¿ciclos reales vs ciclos del modelo?

Un evento de pérdida puede descartar varios paquetes seguidos, así que los descartes por segundo no se pueden comparar con los ciclos del modelo. Lo que sí cuenta eventos son las **reducciones bruscas de la ventana** vistas en `ss` (cwnd cae más de 25 % entre dos muestras para Reno, más de 15 % para Cubic; muestreo de 200 ms, cuenta aproximada). Si «reducciones medidas / ciclos del fluido» ≈ 1, las pérdidas ocurren una por ciclo como supone el modelo; si es claramente > 1, la cola se desborda antes de lo previsto (ráfagas, que el modelo no tiene) o hay timeouts. «Descartes por evento» dice cuántos paquetes se pierden en cada uno. BBR no reduce la ventana a escalones, por eso no tiene esta columna.

| Algoritmo | b | Descartes del tbf (por s) | Retransmisiones (por s, ss) | Reducciones de ventana (por s, ss) | Ciclos por s del fluido | Reducciones medidas / ciclos del fluido | Descartes por evento | Ciclo del fluido (s) | Pérdida en el tbf (%) |
|---|---|---|---|---|---|---|---|---|---|
| bbr | 0.05 | 272.01 | 271.01 | — | — | — | — | — | 3.293 |
| bbr | 0.1 | 299.60 | 298.03 | — | — | — | — | — | 3.615 |
| bbr | 0.25 | 183.29 | 182.04 | — | — | — | — | — | 2.225 |
| bbr | 0.5 | 0.00 | 0.00 | — | — | — | — | — | 0.000 |
| bbr | 1.0 | 0.00 | 0.00 | — | — | — | — | — | 0.000 |
| bbr | 2.0 | 0.00 | 0.00 | — | — | — | — | — | 0.000 |
| cubic | 0.05 | 7.69 | 7.68 | 0.77 | 0.50 | 1.53 | 10.0 | 2.00 | 0.453 |
| cubic | 0.1 | 2.36 | 2.35 | 0.52 | 0.47 | 1.10 | 4.5 | 2.11 | 0.040 |
| cubic | 0.25 | 0.38 | 0.38 | 0.38 | 0.40 | 0.94 | 1.0 | 2.50 | 0.005 |
| cubic | 0.5 | 0.26 | 0.26 | 0.26 | 0.26 | 1.00 | 1.0 | 3.81 | 0.003 |
| cubic | 1.0 | 0.19 | 0.19 | 0.19 | 0.16 | 1.16 | 1.0 | 6.15 | 0.002 |
| cubic | 2.0 | 0.20 | 0.20 | 0.10 | 0.15 | 0.69 | 1.9 | 6.67 | 0.002 |
| reno | 0.05 | 6.00 | 5.99 | 0.96 | 0.57 | 1.66 | 6.3 | 1.74 | 0.241 |
| reno | 0.1 | 2.15 | 2.11 | 0.58 | 0.55 | 1.05 | 3.7 | 1.82 | 0.037 |
| reno | 0.25 | 0.81 | 0.81 | 0.45 | 0.46 | 0.97 | 1.8 | 2.16 | 0.011 |
| reno | 0.5 | 0.38 | 0.38 | 0.34 | 0.35 | 0.98 | 1.1 | 2.86 | 0.005 |
| reno | 1.0 | 0.35 | 0.35 | 0.19 | 0.20 | 0.94 | 1.9 | 5.00 | 0.004 |
| reno | 2.0 | 0.09 | 0.09 | 0.09 | 0.09 | 0.98 | 1.0 | 11.43 | 0.001 |

## ANOVA de dos factores (utilización: algoritmo × b)

| Fuente | F | p |
|---|---|---|
| C(algo) | 137.61 | <0.001 |
| C(bf) | 298.79 | <0.001 |
| C(algo):C(bf) | 75.66 | <0.001 |
| Residual | — | — |

Supuestos: Shapiro-Wilk de residuos p = <0.001; Levene p = 0.336. Si alguno es < 0.05, el ANOVA no es confiable: Kruskal-Wallis entre «algo» (grupos de 18 observaciones): p = 0.321.

### Chequeo de CPU

Governor del host: performance; no_turbo: [1]. 

Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:

| CPU | Mín | Media | Máx |
|---|---|---|---|
| 0 | 364 | 1644 | 2810 |
| 1 | 391 | 2660 | 2807 |
| 2 | 382 | 2081 | 2806 |
| 3 | 390 | 1904 | 2807 |

Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):

| Contenedor | Media % | Máx % |
|---|---|---|
| cli | 7.4 | 9.7 |
| r_bn | 4.9 | 5.1 |
| r_wan | 2.7 | 2.7 |
| srv | 6.3 | 7.4 |
