# Resumen Estadistico - Experimento H5 (Balanceo con HAProxy)

## 1. Calibracion Base y Validacion de Simuladores
- **Servicio vacio por servidor usado (s0):** `0.6087 ms` (handler=`0.0032`, cgroup=`0.6087`)
- **Latencia externa vacia a traves de HAProxy (c0_lb):** `0.4635 ms`
- **Salto ida/vuelta adicional HAProxy <-> API (gap_lb_api):** `0.2546 ms`
- **Validacion sintetica `random` vs. P-K:** Teo=`40.00 ms`, Sim=`38.73 ms` (`-3.16%`)
- **Validacion sintetica `central` vs. Erlang C:** Teo=`15.09 ms`, Sim=`14.73 ms` (`-2.38%`)

## 2. Chequeo de Utilizacion (dos caminos)
`rho_med` = lambda*E[S]/K desde los pedidos; `rho_cg` = CPU del cgroup / duracion. Si difieren mucho, el s0 usado no representa el servicio real.

| K | rho_obj | rho_med | rho_cg (prom) | rho_cg (max servidor) | s0 efectivo cgroup (ms) |
|---:|---:|---:|---:|---:|---:|
| 2 | 0.6 | 0.601 | 0.609 | 0.688 | 0.817 |
| 2 | 0.9 | 0.899 | 0.895 | 0.995 | 0.667 |
| 4 | 0.6 | 0.601 | 0.609 | 0.704 | 0.799 |
| 4 | 0.9 | 0.900 | 0.896 | 0.993 | 0.704 |

## 3. Comparacion de Politicas: Medido vs. Simulacion vs. P-K / Erlang C
E[T] medido con IC 95% t de Student entre repeticiones. `Sim traza` usa gap de red para `central`.

| K | rho_obj | Politica | n | E[T] Medido ± IC (ms) | Sim traza (ms) | Sim asig. real (ms) | Medido/Sim | Teo P-K / Erlang C (ms) |
|---:|---:|:---|---:|---:|---:|---:|---:|---:|
| 2 | 0.6 | random | 3 | 28.89 ± 2.49 | 26.02 | - | 1.11 | 25.46 (P-K) |
| 2 | 0.6 | roundrobin | 3 | 21.59 ± 0.56 | 20.82 | - | 1.04 | - |
| 2 | 0.6 | random2 | 3 | 20.95 ± 1.56 | 17.79 | - | 1.18 | - |
| 2 | 0.6 | leastconn | 3 | 18.55 ± 0.83 | 17.76 | - | 1.04 | - |
| 2 | 0.6 | central | 3 | 18.38 ± 2.02 | 17.71 | - | 1.04 | 17.24 (Erlang C) |
| 2 | 0.9 | random | 3 | 1141.61 ± 1038.85 | 99.02 | - | 11.53 | 101.52 (P-K) |
| 2 | 0.9 | roundrobin | 3 | 78.94 ± 32.25 | 75.76 | - | 1.04 | - |
| 2 | 0.9 | random2 | 3 | 64.05 ± 20.47 | 57.72 | - | 1.11 | - |
| 2 | 0.9 | leastconn | 3 | 56.47 ± 9.07 | 54.75 | - | 1.03 | - |
| 2 | 0.9 | central | 3 | 60.74 ± 16.19 | 60.18 | - | 1.01 | 55.67 (Erlang C) |
| 4 | 0.6 | random | 3 | 29.06 ± 1.80 | 26.03 | - | 1.12 | 26.26 (P-K) |
| 4 | 0.6 | roundrobin | 3 | 18.45 ± 0.79 | 17.89 | - | 1.03 | - |
| 4 | 0.6 | random2 | 3 | 18.31 ± 0.24 | 16.16 | - | 1.13 | - |
| 4 | 0.6 | leastconn | 3 | 14.71 ± 0.34 | 14.11 | - | 1.04 | - |
| 4 | 0.6 | central | 3 | 13.34 ± 0.25 | 12.88 | - | 1.04 | 12.85 (Erlang C) |
| 4 | 0.9 | random | 3 | 1539.28 ± 253.93 | 95.71 | - | 16.08 | 99.59 (P-K) |
| 4 | 0.9 | roundrobin | 3 | 73.01 ± 5.20 | 68.45 | - | 1.07 | - |
| 4 | 0.9 | random2 | 3 | 43.83 ± 7.90 | 36.75 | - | 1.19 | - |
| 4 | 0.9 | leastconn | 3 | 34.51 ± 3.14 | 32.19 | - | 1.07 | - |
| 4 | 0.9 | central | 3 | 40.04 ± 8.07 | 38.35 | - | 1.04 | 33.24 (Erlang C) |

## 4. Reparto de Pedidos por Servidor y Efecto Ociosidad en `maxconn 1`
| K | rho_obj | Politica | Reparto Medido (%) | Desbalance Max-Min (%) |
|---:|---:|:---|:---|---:|
| 2 | 0.6 | random | 43.1% / 56.9% | 13.87% |
| 2 | 0.6 | roundrobin | 50.0% / 50.0% | 0.00% |
| 2 | 0.6 | random2 | 46.8% / 53.2% | 6.32% |
| 2 | 0.6 | leastconn | 50.0% / 50.0% | 0.42% |
| 2 | 0.6 | central | 49.8% / 50.2% | 0.43% |
| 2 | 0.9 | random | 43.0% / 57.0% | 13.94% |
| 2 | 0.9 | roundrobin | 50.0% / 50.0% | 0.00% |
| 2 | 0.9 | random2 | 48.8% / 51.2% | 2.30% |
| 2 | 0.9 | leastconn | 50.0% / 50.0% | 0.45% |
| 2 | 0.9 | central | 49.8% / 50.2% | 0.66% |
| 4 | 0.6 | random | 28.8% / 18.4% / 24.3% / 28.5% | 10.38% |
| 4 | 0.6 | roundrobin | 25.0% / 25.0% / 25.0% / 25.0% | 0.00% |
| 4 | 0.6 | random2 | 27.0% / 21.4% / 24.7% / 26.9% | 5.59% |
| 4 | 0.6 | leastconn | 24.9% / 25.1% / 25.1% / 24.9% | 0.36% |
| 4 | 0.6 | central | 24.9% / 24.9% / 25.2% / 24.9% | 0.44% |
| 4 | 0.9 | random | 28.9% / 18.6% / 24.2% / 28.3% | 10.29% |
| 4 | 0.9 | roundrobin | 25.0% / 25.0% / 25.0% / 25.0% | 0.00% |
| 4 | 0.9 | random2 | 26.0% / 23.3% / 25.1% / 25.6% | 2.73% |
| 4 | 0.9 | leastconn | 24.9% / 25.0% / 25.1% / 24.9% | 0.39% |
| 4 | 0.9 | central | 25.0% / 25.0% / 24.9% / 25.2% | 0.38% |

### Impacto del tiempo ocioso entre pedidos en `central` (`maxconn 1`)
| K | rho_obj | Erlang C Ideal (ms) | Sim `central` ideal (ms) | Sim `central` + Gap Red (ms) | Medido `central` (ms) | Medido `leastconn` (ms) | Penalizacion `maxconn 1` vs `leastconn` (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 0.6 | 17.24 | 17.20 | 17.71 | 18.38 | 18.55 | -0.17 |
| 2 | 0.9 | 55.67 | 48.98 | 60.18 | 60.74 | 56.47 | +4.26 |
| 4 | 0.6 | 12.85 | 12.69 | 12.88 | 13.34 | 14.71 | -1.36 |
| 4 | 0.9 | 33.24 | 31.63 | 38.35 | 40.04 | 34.51 | +5.53 |
