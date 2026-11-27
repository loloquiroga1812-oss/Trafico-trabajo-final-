# T5(a) — N flujos del mismo algoritmo: índice de Jain

RTT = 20 ms, buffer = BDP, `iperf3 -P N` (todos los flujos con el mismo algoritmo y RTT). **Jain** J = (Σxᵢ)²/(n·Σxᵢ²) ∈ [1/n, 1]: 1 = reparto exacto. Cada corrida usa el goodput medio de cada flujo (sin el arranque); IC95 t entre repeticiones.

| Algoritmo | N | n | Jain | ± IC95 | Mínimo posible 1/N | Flujo menor / media | Flujo mayor / media | Mayor / menor | Utilización (%) | ± IC95 | Cola media / BDP | Descartes tbf (por s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bbr | 2 | 3 | 0.9998 | 0.0005 | 0.500 | 0.99 | 1.01 | 1.02 | 98.20 | 0.07 | 0.878 | 349.3 |
| bbr | 4 | 3 | 0.9950 | 0.0048 | 0.250 | 0.91 | 1.07 | 1.18 | 98.32 | 0.00 | 0.945 | 716.9 |
| bbr | 8 | 3 | 0.9147 | 0.1525 | 0.125 | 0.49 | 1.35 | 3.19 | 98.51 | 0.11 | 0.959 | 1110.8 |
| cubic | 2 | 3 | 0.9942 | 0.0172 | 0.500 | 0.93 | 1.07 | 1.15 | 100.01 | 0.03 | 0.806 | 0.8 |
| cubic | 4 | 3 | 0.9945 | 0.0086 | 0.250 | 0.90 | 1.09 | 1.21 | 100.01 | 0.05 | 0.836 | 3.1 |
| cubic | 8 | 3 | 0.9961 | 0.0045 | 0.125 | 0.91 | 1.11 | 1.23 | 100.00 | 0.03 | 0.859 | 10.1 |
| reno | 2 | 3 | 0.9986 | 0.0031 | 0.500 | 0.97 | 1.03 | 1.07 | 99.98 | 0.03 | 0.618 | 1.3 |
| reno | 4 | 3 | 0.9988 | 0.0015 | 0.250 | 0.96 | 1.05 | 1.09 | 99.98 | 0.05 | 0.656 | 4.6 |
| reno | 8 | 3 | 0.9914 | 0.0091 | 0.125 | 0.88 | 1.17 | 1.33 | 100.00 | 0.07 | 0.692 | 16.8 |

## ANOVA de dos factores sobre el índice de Jain: algoritmo × N

| Fuente | F | p |
|---|---|---|
| C(algo) | 4.66 | 0.023 |
| C(Nf) | 6.08 | 0.010 |
| C(algo):C(Nf) | 5.04 | 0.007 |
| Residual | — | — |

Supuestos: Shapiro-Wilk de residuos p = <0.001; Levene p = 0.012. Si alguno es < 0.05, el ANOVA no es confiable: Kruskal-Wallis entre «algo» (grupos de 9 observaciones): p = 0.558.

### Chequeo de CPU

Governor del host: performance; no_turbo: [1]. 

Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:

| CPU | Mín | Media | Máx |
|---|---|---|---|
| 0 | 397 | 987 | 2809 |
| 1 | 396 | 2618 | 2801 |
| 2 | 393 | 1454 | 2801 |
| 3 | 395 | 874 | 2826 |

Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):

| Contenedor | Media % | Máx % |
|---|---|---|
| cli | 6.8 | 8.2 |
| r_bn | 4.4 | 4.8 |
| r_wan | 2.5 | 2.6 |
| srv | 2.9 | 3.2 |
