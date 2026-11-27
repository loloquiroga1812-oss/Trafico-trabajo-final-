# T2 — Ventana fija: x = min(η·C, W/RTT)

C = 100 Mbit/s, η·C = 95.64 Mbit/s. W es la ventana pedida con `iperf3 -w` (en KiB). El buffer del tbf es de 4 MB, así que nunca hay pérdida y la cola puede crecer cuando W supera el BDP. `srtt` es el RTT suavizado del emisor, muestreado con `ss` cada 200 ms durante la medición.

Topes de buffer de socket vistos en los contenedores: wmem_max = [16777216], rmem_max = [16777216]. Linux recorta lo que pide `-w` a estos valores (y luego duplica el resultado).

## Goodput por condición (algoritmo Cubic)

Media de repeticiones con IC95 t entre repeticiones. «Teórico» = min(η·C, W/RTT configurado). Zona: *ventana* si W/RTT ≤ 50%·η·C (región donde se ajusta la recta), *enlace* si W/RTT ≥ η·C, *codo* en el medio.

«Teórico con W efectiva» = min(η·C, snd_wnd/srtt), donde snd_wnd es la ventana que anuncia el receptor, medida con `ss` en el emisor. Sale de una medición distinta del goodput, así que es una prueba no circular de que el modelo vale con la ventana que Linux usa de verdad.

| W (KiB) | RTT config (ms) | n | Goodput (Mbit/s) | ± IC95 | W/RTT (Mbit/s) | Teórico | Error % | Teórico con W efectiva | Error % (W efectiva) | srtt (ms) | srtt − RTT config (ms) | Zona | Ventana recortada |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 32 | 10 | 3 | 28.99 | 0.32 | 26.21 | 26.21 | 10.6 | 30.59 | -5.2 | 10.23 | 0.23 | ventana |  |
| 64 | 10 | 3 | 57.70 | 0.11 | 52.43 | 52.43 | 10.1 | 60.13 | -4.0 | 10.39 | 0.39 | codo |  |
| 128 | 10 | 3 | 95.58 | 0.00 | 104.86 | 95.64 | -0.1 | 95.64 | -0.1 | 12.60 | 2.60 | enlace |  |
| 256 | 10 | 3 | 95.65 | 0.00 | 209.72 | 95.64 | 0.0 | 95.64 | 0.0 | 27.46 | 17.46 | enlace |  |
| 512 | 10 | 3 | 95.65 | 0.00 | 419.43 | 95.64 | 0.0 | 95.64 | 0.0 | 51.87 | 41.87 | enlace |  |
| 1024 | 10 | 3 | 95.65 | 0.00 | 838.86 | 95.64 | 0.0 | 95.64 | 0.0 | 104.28 | 94.28 | enlace |  |
| 32 | 20 | 3 | 14.71 | 0.11 | 13.11 | 13.11 | 12.2 | 15.08 | -2.5 | 20.23 | 0.23 | ventana |  |
| 64 | 20 | 3 | 30.01 | 0.39 | 26.21 | 26.21 | 14.5 | 30.65 | -2.1 | 20.28 | 0.28 | ventana |  |
| 128 | 20 | 3 | 59.50 | 0.11 | 52.43 | 52.43 | 13.5 | 61.22 | -2.8 | 20.42 | 0.42 | codo |  |
| 256 | 20 | 3 | 95.65 | 0.01 | 104.86 | 95.64 | 0.0 | 95.64 | 0.0 | 25.69 | 5.69 | enlace |  |
| 512 | 20 | 3 | 95.65 | 0.00 | 209.72 | 95.64 | 0.0 | 95.64 | 0.0 | 51.88 | 31.88 | enlace |  |
| 1024 | 20 | 3 | 95.65 | 0.00 | 419.43 | 95.64 | 0.0 | 95.64 | 0.0 | 104.28 | 84.28 | enlace |  |
| 32 | 50 | 3 | 5.77 | 0.00 | 5.24 | 5.24 | 10.0 | 5.90 | -2.3 | 51.01 | 1.01 | ventana |  |
| 64 | 50 | 3 | 12.31 | 0.11 | 10.49 | 10.49 | 17.4 | 12.39 | -0.7 | 50.28 | 0.28 | ventana |  |
| 128 | 50 | 3 | 24.02 | 0.11 | 20.97 | 20.97 | 14.5 | 24.78 | -3.1 | 50.36 | 0.36 | ventana |  |
| 256 | 50 | 3 | 48.81 | 0.21 | 41.94 | 41.94 | 16.4 | 49.64 | -1.7 | 50.42 | 0.42 | ventana |  |
| 512 | 50 | 3 | 95.63 | 0.09 | 83.89 | 83.89 | 14.0 | 95.64 | -0.0 | 51.86 | 1.86 | codo |  |
| 1024 | 50 | 3 | 95.65 | 0.00 | 167.77 | 95.64 | 0.0 | 95.64 | 0.0 | 104.30 | 54.30 | enlace |  |
| 32 | 100 | 3 | 2.92 | 0.00 | 2.62 | 2.62 | 11.4 | 2.98 | -2.0 | 101.03 | 1.03 | ventana |  |
| 64 | 100 | 3 | 6.12 | 0.11 | 5.24 | 5.24 | 16.7 | 6.18 | -1.1 | 100.49 | 0.49 | ventana |  |
| 128 | 100 | 3 | 12.21 | 0.00 | 10.49 | 10.49 | 16.4 | 12.44 | -1.8 | 100.32 | 0.32 | ventana |  |
| 256 | 100 | 3 | 24.54 | 0.11 | 20.97 | 20.97 | 17.0 | 24.92 | -1.5 | 100.37 | 0.37 | ventana |  |
| 512 | 100 | 3 | 49.46 | 0.11 | 41.94 | 41.94 | 17.9 | 49.87 | -0.8 | 100.43 | 0.43 | ventana |  |
| 1024 | 100 | 3 | 95.65 | 0.00 | 83.89 | 83.89 | 14.0 | 95.64 | 0.0 | 104.27 | 4.27 | codo |  |
| 32 | 200 | 3 | 1.42 | 0.00 | 1.31 | 1.31 | 8.6 | 1.49 | -4.3 | 202.48 | 2.48 | ventana |  |
| 64 | 200 | 3 | 3.07 | 0.00 | 2.62 | 2.62 | 17.2 | 3.10 | -0.8 | 200.65 | 0.65 | ventana |  |
| 128 | 200 | 3 | 6.14 | 0.00 | 5.24 | 5.24 | 17.2 | 6.14 | 0.0 | 203.16 | 3.16 | ventana |  |
| 256 | 200 | 3 | 12.46 | 0.11 | 10.49 | 10.49 | 18.8 | 12.48 | -0.1 | 200.64 | 0.64 | ventana |  |
| 512 | 200 | 3 | 24.79 | 0.00 | 20.97 | 20.97 | 18.2 | 24.99 | -0.8 | 200.43 | 0.43 | ventana |  |
| 1024 | 200 | 3 | 49.76 | 0.10 | 41.94 | 41.94 | 18.6 | 49.97 | -0.4 | 200.53 | 0.53 | ventana |  |

## Ley de potencia: log x contra log(W/RTT), solo región limitada por ventana

| Condiciones | Variable explicativa | N | Pendiente | ± IC95 | p (H0: pendiente = 1) | R² | RMSE (log) | Factor k (x ≈ k·W/RTT) |
|---|---|---|---|---|---|---|---|---|
| todas | RTT configurado | 18 | 1.012 | 0.013 | 0.069 | 0.99941 | 0.0249 | 1.119 |
| todas | srtt del emisor | 18 | 1.011 | 0.011 | 0.045 | 0.99960 | 0.0205 | 1.131 |
| sin recortadas | RTT configurado | 18 | 1.012 | 0.013 | 0.069 | 0.99941 | 0.0249 | 1.119 |
| sin recortadas | srtt del emisor | 18 | 1.011 | 0.011 | 0.045 | 0.99960 | 0.0205 | 1.131 |

Unidad de ajuste: la media de cada condición (las repeticiones de una misma condición no son puntos independientes de la recta). Con pendiente ≈ 1, k es la razón entre la ventana efectiva y la pedida. Con 3 repeticiones y varianza casi nula, un p pequeño no implica un desvío importante: mirar pendiente, IC y k. RMSE menor = la variable explica mejor.

## Ventana efectiva: ¿cuánta ventana usa Linux realmente?

Por cada W pedida (condiciones en zona *ventana*): «x·srtt/W» es la ventana efectiva por la ley de Little (bytes en vuelo = goodput × RTT) dividida por W; «sndbuf/W» es el buffer de envío del socket (`tb` de ss) y «snd_wnd/W» la ventana que anuncia el receptor.

| W pedida (KiB) | Corridas | x·srtt/W | sndbuf/W | snd_wnd/W | máx en vuelo/W | sndbuf (KiB) |
|---|---|---|---|---|---|---|
| 32 | 15 | 1.12 | 2.00 | 1.16 | 1.19 | 64 |
| 64 | 12 | 1.17 | 2.00 | 1.19 | 1.21 | 128 |
| 128 | 9 | 1.17 | 2.00 | 1.19 | 1.20 | 256 |
| 256 | 9 | 1.18 | 2.00 | 1.19 | 1.20 | 512 |
| 512 | 6 | 1.18 | 2.00 | 1.19 | 1.20 | 1024 |
| 1024 | 3 | 1.19 | 2.00 | 1.19 | 1.19 | 2048 |

Si «sndbuf/W» da ≈ 2, es el duplicado que hace el kernel. Si para W grandes el sndbuf deja de crecer, se llegó al tope wmem_max y la ventana pedida no es la usada.

## Régimen de enlace: RTT configurado vs srtt

| W (KiB) | RTT config | Goodput | η·C | srtt (ms) | Cola observada (ms) = srtt − RTT | Cola predicha (ms) = snd_wnd/(η·C) − RTT | x·srtt/W |
|---|---|---|---|---|---|---|---|
| 128 | 10 | 95.58 | 95.64 | 12.60 | 2.6 | 3.0 | 1.15 |
| 256 | 10 | 95.65 | 95.64 | 27.46 | 17.5 | 18.4 | 1.25 |
| 256 | 20 | 95.65 | 95.64 | 25.69 | 5.7 | 6.2 | 1.17 |
| 512 | 10 | 95.65 | 95.64 | 51.87 | 41.9 | 42.4 | 1.18 |
| 512 | 20 | 95.65 | 95.64 | 51.88 | 31.9 | 32.4 | 1.18 |
| 1024 | 10 | 95.65 | 95.64 | 104.28 | 94.3 | 94.8 | 1.19 |
| 1024 | 20 | 95.65 | 95.64 | 104.28 | 84.3 | 84.8 | 1.19 |
| 1024 | 50 | 95.65 | 95.64 | 104.30 | 54.3 | 54.8 | 1.19 |

La cola predicha usa solo la ventana anunciada y la capacidad (no el srtt), así que comparar con la observada sí es una verificación independiente. 
Cuando W/RTT configurado supera η·C, el enlace manda: el goodput se clava en η·C y el exceso de ventana se acumula como cola (srtt − RTT). Por la ley de Little, goodput × srtt = bytes en vuelo, así que «x·srtt/W» es la ventana efectiva sobre la pedida y esa cuenta cierra siempre: reformula el modelo, no lo verifica de forma independiente.

## ¿Importa el algoritmo cuando manda la ventana?

Razón x / (W/RTT configurado) por condición y algoritmo (1 = el modelo se cumple exacto).

| Condición (W_RTT) | Algoritmo | n | Razón media | ± IC95 | Goodput (Mbit/s) |
|---|---|---|---|---|---|
| 128k_50ms | bbr | 3 | 1.0024 | 0.0052 | 21.02 |
| 128k_50ms | cubic | 3 | 1.1453 | 0.0052 | 24.02 |
| 128k_50ms | reno | 3 | 1.1453 | 0.0052 | 24.02 |
| 256k_100ms | bbr | 3 | 1.0834 | 0.0102 | 22.72 |
| 256k_100ms | cubic | 3 | 1.1703 | 0.0051 | 24.54 |
| 256k_100ms | reno | 3 | 1.1751 | 0.0003 | 24.64 |
| 64k_100ms | bbr | 3 | 1.0572 | 0.0000 | 5.54 |
| 64k_100ms | cubic | 3 | 1.1667 | 0.0205 | 6.12 |
| 64k_100ms | reno | 3 | 1.1620 | 0.0206 | 6.09 |
| 64k_20ms | bbr | 3 | 1.1162 | 0.0040 | 29.26 |
| 64k_20ms | cubic | 3 | 1.1448 | 0.0148 | 30.01 |
| 64k_20ms | reno | 3 | 1.1410 | 0.0108 | 29.91 |

ANOVA (efecto aditivo del algoritmo, controlando por condición):

| Fuente | F | p |
|---|---|---|
| C(algo) | 68.27 | <0.001 |
| C(cond) | 7.10 | <0.001 |
| Residual | — | — |

Supuestos: Shapiro-Wilk de residuos p = 0.261, Levene p = 0.877. Kruskal-Wallis entre algoritmos (sobre la razón normalizada): p = <0.001 (cada algoritmo reúne 12 observaciones, todas las condiciones juntas).

### Chequeo de CPU

Governor del host: performance; no_turbo: [1]. 

Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:

| CPU | Mín | Media | Máx |
|---|---|---|---|
| 0 | 367 | 1452 | 2808 |
| 1 | 396 | 2127 | 2806 |
| 2 | 389 | 1503 | 2808 |
| 3 | 388 | 1554 | 2809 |

Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):

| Contenedor | Media % | Máx % |
|---|---|---|
| cli | 5.8 | 13.2 |
| r_bn | 2.8 | 3.6 |
| r_wan | 2.8 | 3.6 |
| srv | 6.9 | 9.4 |
