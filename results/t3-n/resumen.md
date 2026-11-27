# T3 — N flujos Reno: regla BDP/√N y sincronización

C = 100 Mbit/s, RTT = 20 ms, BDP = 250 kB, N flujos Reno (`iperf3 -P N`), 60 s de medición. IC95 t entre repeticiones.

**Modelos de comparación.** *Fluido sincronizado*: en cada pérdida los N flujos reducen su ventana (la suma se comporta como un solo diente de sierra: da lo mismo que un flujo con el mismo b). *Fluido «un flujo por pérdida»*: en cada pérdida reduce un solo flujo, elegido en proporción a su ventana; es el extremo más regulado: la ventana total casi no oscila (sus ρ y r salen en la tabla de sincronización, con r negativa). Entre los dos queda el caso de fases independientes (ρ ≈ 1, r ≈ 0), que es el supuesto de Appenzeller y no coincide con ninguno de los dos modelos de fluido. *Cota de Appenzeller et al. 2004 (Eq. 3)*: Util ≥ erf(3√3/(2√2) · √N · b/(1+b)); es una cota inferior que supone muchos flujos independientes (el artículo la valida con cientos de flujos y dice que con menos de ~100 hay sincronización parcial).

## Utilización y cola

| N | b = B/BDP | n | Utilización medida (%) | ± IC95 | Fluido sincr. (%) | Fluido desincr. (%) | Cota Appenzeller (%) | Cola media / BDP | ± IC95 | Fluido sincr. | Fluido desincr. | Descartes tbf (por s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 0.1 | 3 | 87.47 | 0.48 | 81.74 | 98.35 | 36.33 | 0.020 | 0.001 | 0.010 | 0.033 | 14.0 |
| 4 | 0.25 | 3 | 94.46 | 1.34 | 89.31 | 99.97 | 70.13 | 0.088 | 0.007 | 0.056 | 0.158 | 11.1 |
| 4 | 0.5 | 3 | 98.38 | 0.35 | 96.44 | 100.00 | 91.67 | 0.247 | 0.015 | 0.190 | 0.389 | 8.1 |
| 4 | 1.0 | 3 | 99.97 | 0.03 | 100.00 | 100.00 | 99.06 | 0.650 | 0.028 | 0.556 | 0.852 | 4.6 |
| 16 | 0.1 | 3 | 94.46 | 0.84 | 81.74 | 100.00 | 65.52 | 0.040 | 0.004 | 0.010 | 0.078 | 143.7 |
| 16 | 0.25 | 3 | 97.95 | 0.26 | 89.27 | 100.00 | 96.23 | 0.129 | 0.006 | 0.056 | 0.225 | 123.0 |
| 16 | 0.5 | 3 | 99.83 | 0.05 | 96.42 | 100.00 | 99.95 | 0.329 | 0.006 | 0.190 | 0.470 | 93.5 |
| 16 | 1.0 | 3 | 99.98 | 0.03 | 100.00 | 100.00 | 100.00 | 0.760 | 0.006 | 0.555 | 0.960 | 57.3 |

## ¿Alcanza B = BDP/√N?

| N | b = 1/√N | b medido (más cercano) | Utilización medida (%) | ± IC95 | Cota Appenzeller (%) | Fluido sincr. (%) | Fluido desincr. (%) | Límite inferior del IC ≥ 95 % | ≥ 99 % |
|---|---|---|---|---|---|---|---|---|---|
| 4 | 0.500 | 0.5 | 98.38 | 0.35 | 91.67 | 96.44 | 100.00 | sí | no |
| 16 | 0.250 | 0.25 | 97.95 | 0.26 | 96.23 | 89.27 | 100.00 | sí | no |

Si la utilización medida queda cerca del fluido *sincronizado* y lejos del desincronizado, la regla √N no se cumple porque los flujos se mueven juntos.

## ¿Se sincronizan los flujos?

Se mide con la ventana de cada flujo (muestreada cada 50 ms con `ss`). **ρ** = CV de la ventana total · √N / CV medio de un flujo: ≈ 1 si las fases son independientes, ≈ √N si están en fase y < 1 si los flujos se compensan entre sí (un flujo baja mientras otro sube). **r** = correlación media entre pares de flujos; su p sale de desplazar circularmente la serie de cada flujo un tiempo al azar (2000 veces), que conserva la forma de cada diente de sierra y destruye la fase relativa. La columna «reps con p < 0.05» cuenta en cuántas repeticiones r es mayor que lo esperable sin sincronía. Con 50 ms de muestreo y ciclos de ~0.2 s por flujo (N = 16) la resolución es gruesa: tomarlo como indicador.

| N | b | ρ medido | ± IC95 | ρ fluido sincr. | ρ fluido 1-por-pérdida | r medio | ± IC95 | r fluido sincr. | r fluido 1-por-pérdida | Reps con p < 0.05 | Índice de Jain | ± IC95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 0.1 | 1.04 | 0.03 | 2.00 | 0.31 | 0.028 | 0.018 | 1.00 | -0.30 | 0/3 | 0.9989 | 0.0016 |
| 4 | 0.25 | 1.05 | 0.15 | 2.00 | 0.30 | 0.036 | 0.106 | 1.00 | -0.30 | 1/3 | 0.9995 | 0.0005 |
| 4 | 0.5 | 1.07 | 0.16 | 2.00 | 0.32 | 0.050 | 0.111 | 1.00 | -0.30 | 2/3 | 0.9983 | 0.0025 |
| 4 | 1.0 | 1.08 | 0.24 | 2.00 | 0.32 | 0.063 | 0.175 | 1.00 | -0.29 | 2/3 | 0.9980 | 0.0028 |
| 16 | 0.1 | 0.83 | 0.12 | 4.00 | 0.14 | -0.019 | 0.014 | 1.00 | -0.06 | 0/3 | 0.9963 | 0.0039 |
| 16 | 0.25 | 0.96 | 0.05 | 4.00 | 0.15 | -0.005 | 0.005 | 1.00 | -0.06 | 0/3 | 0.9969 | 0.0028 |
| 16 | 0.5 | 0.96 | 0.03 | 4.00 | 0.15 | -0.004 | 0.004 | 1.00 | -0.06 | 0/3 | 0.9960 | 0.0040 |
| 16 | 1.0 | 0.99 | 0.03 | 4.00 | 0.15 | 0.001 | 0.006 | 1.00 | -0.06 | 0/3 | 0.9920 | 0.0068 |

## Frecuencia de reducciones de ventana

Suma sobre los N flujos de las reducciones bruscas de cwnd por segundo (más de 25 % entre dos muestras de 50 ms; cuenta aproximada). En el fluido «un flujo por pérdida» cada evento reduce un flujo; en el sincronizado cada evento reduce N. Que las dos columnas del fluido den parecido es normal (la ventana que hay que descontar por segundo es la misma): este cuadro es una verificación de consistencia, no discrimina entre modelos. Quien discrimina es ρ y r.

| N | b | Reducciones medidas (por s) | Fluido 1-por-pérdida (eventos por s) | Fluido sincr. (N × eventos por s) | Descartes del tbf (por s) |
|---|---|---|---|---|---|
| 4 | 0.1 | 7.2 | 7.6 | 8.7 | 14.0 |
| 4 | 0.25 | 6.0 | 6.0 | 7.4 | 11.1 |
| 4 | 0.5 | 4.6 | 4.2 | 5.5 | 8.1 |
| 4 | 1.0 | 2.7 | 2.3 | 3.2 | 4.6 |
| 16 | 0.1 | 71.3 | 113.4 | 139.6 | 143.7 |
| 16 | 0.25 | 63.3 | 87.9 | 118.0 | 123.0 |
| 16 | 0.5 | 50.1 | 60.8 | 88.6 | 93.5 |
| 16 | 1.0 | 31.8 | 34.1 | 51.6 | 57.3 |

## ANOVA de dos factores (utilización: N × b)

| Fuente | F | p |
|---|---|---|
| C(Nf) | 902.72 | <0.001 |
| C(bf) | 1671.97 | <0.001 |
| C(Nf):C(bf) | 231.98 | <0.001 |
| Residual | — | — |

Supuestos: Shapiro-Wilk de residuos p = 0.011; Levene p = 0.144. Si alguno es < 0.05, el ANOVA no es confiable: Kruskal-Wallis entre «Nf» (grupos de 12 observaciones): p = 0.225.

### Chequeo de CPU

Governor del host: performance; no_turbo: [1]. 

Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:

| CPU | Mín | Media | Máx |
|---|---|---|---|
| 0 | 391 | 1810 | 2809 |
| 1 | 400 | 2769 | 2802 |
| 2 | 392 | 1941 | 2822 |
| 3 | 390 | 2008 | 2811 |

Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):

| Contenedor | Media % | Máx % |
|---|---|---|
| cli | 9.2 | 9.7 |
| r_bn | 5.1 | 5.3 |
| r_wan | 2.7 | 2.8 |
| srv | 16.4 | 17.7 |
