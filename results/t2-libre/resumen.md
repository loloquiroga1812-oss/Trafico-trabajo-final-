# T2 — Ventana libre con buffer = BDP

RTT = 20 ms, C = 100 Mbit/s, buffer del tbf = BDP = 250 kB. Sin `-w`: la ventana la gobierna el algoritmo. La cola es el backlog del tbf (`tc -s -j`, cada 100 ms); la unidad estadística es la media de cada corrida (las muestras de una corrida están correlacionadas), con IC95 t entre repeticiones.

| Algoritmo | n | Utilización (% de η·C) | ± IC95 | Cola media (kB) | ± IC95 | Cola media / BDP | Retardo de cola medio (ms) | ± IC95 | Cola p95 / BDP | Cola máx / BDP | Pérdida en el tbf (%) | ± IC95 | srtt medio (ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bbr | 3 | 98.15 | 0.00 | 39.6 | 1.5 | 0.16 | 3.17 | 0.12 | 0.35 | 0.42 | 0.000 | 0.000 | 23.28 |
| cubic | 3 | 99.99 | 0.00 | 196.9 | 1.0 | 0.79 | 15.75 | 0.08 | 0.98 | 1.00 | 0.002 | 0.000 | 35.89 |
| reno | 3 | 99.92 | 0.00 | 132.3 | 0.3 | 0.53 | 10.58 | 0.02 | 0.93 | 1.00 | 0.004 | 0.001 | 30.72 |

«Utilización» es el goodput sobre η·C: 100 % = enlace lleno. La pérdida es la fracción de paquetes que el tbf descartó por buffer lleno durante la medición.

## Por repetición

| Algoritmo | Rep | Utilización % | Cola media / BDP | Pérdida % | Retransmisiones en la ventana |
|---|---|---|---|---|---|
| bbr | 1 | 98.15 | 0.160 | 0.000 | 0 |
| bbr | 2 | 98.15 | 0.156 | 0.000 | 0 |
| bbr | 3 | 98.15 | 0.159 | 0.000 | 0 |
| cubic | 1 | 99.99 | 0.789 | 0.002 | 11 |
| cubic | 2 | 99.99 | 0.786 | 0.002 | 13 |
| cubic | 3 | 99.99 | 0.788 | 0.002 | 11 |
| reno | 1 | 99.92 | 0.529 | 0.004 | 20 |
| reno | 2 | 99.92 | 0.529 | 0.005 | 22 |
| reno | 3 | 99.92 | 0.530 | 0.005 | 22 |

Con 3 repeticiones por algoritmo, el menor p posible de Kruskal-Wallis es 0.027: la comparación entre algoritmos se apoya en los IC, no en un único p.

### Chequeo de CPU

Governor del host: performance; no_turbo: [1]. 

Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:

| CPU | Mín | Media | Máx |
|---|---|---|---|
| 0 | 393 | 1633 | 2805 |
| 1 | 400 | 2771 | 2801 |
| 2 | 392 | 2265 | 2804 |
| 3 | 393 | 1784 | 2805 |

Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):

| Contenedor | Media % | Máx % |
|---|---|---|
| cli | 7.9 | 8.8 |
| r_bn | 4.8 | 5.0 |
| r_wan | 2.6 | 2.7 |
| srv | 6.3 | 7.0 |
