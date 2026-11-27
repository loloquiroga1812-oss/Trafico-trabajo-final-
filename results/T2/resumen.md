# Resumen Estadistico - Experimento T2 (Ventana y BDP)

## 1. Parametros de Referencia
- **Capacidad bruta del enlace (C):** `100.0 Mbit/s`
- **Eficiencia maxima de trama (eta = 1448/1514):** `0.95641` -> **Techo util `eta*C`:** `95.64 Mbit/s`

## 2. Verificacion de la Ventana Efectiva de Linux (`iperf3 -w`)
| W pedida (kB) | Ventana efectiva `snd_wnd` ± IC 95% (kB) | Factor `W_ef / W_pedida` |
|---:|---:|---:|
| 32 | 32.0 ± 0.0 | 1.00x |
| 64 | 64.0 ± 0.0 | 1.00x |
| 128 | 128.0 ± 0.0 | 1.00x |
| 256 | 256.0 ± 0.0 | 1.00x |
| 512 | 512.0 ± 0.0 | 1.00x |
| 1024 | 1024.0 ± 0.0 | 1.00x |

## 3. Regresion Lineal Log-Log en la Region Limitada por Ventana
Puntos analizados donde `W_ef / srtt < 0.85 * eta * C` (`n = 180` corridas). La pendiente teorica esperada de `log10(x)` vs `log10(W/RTT)` es **1.00**:

| Modelo de eje X (`W / RTT`) | Pendiente ± IC 95% | R² | Error porcentual medio (MAPE) |
|:---|---:|---:|---:|
| W pedida / RTT configurado | 1.0089 ± 0.0061 | 0.9983 | 11.97% |
| W efectiva / RTT configurado | 1.0089 ± 0.0061 | 0.9983 | 11.97% |
| W efectiva / srtt medido | 1.0089 ± 0.0061 | 0.9983 | 11.97% |

- **Prueba de Kruskal-Wallis entre algoritmos en region limitada por ventana:** `p = 0.5432` (si `p > 0.05`, el algoritmo de congestion no altera el goodput cuando manda W).

## 4. Ventana Libre con Buffer = BDP (`Reno` vs `Cubic` vs `BBR`)
| RTT cfg (ms) | BDP (kB) | Algoritmo | Goodput ± IC (Mbit/s) | Llenado `x / (eta*C)` | srtt medio (ms) | Cola media TBF (kB) | Cola / BDP (%) | Retransm. |
|---:|---:|:---|---:|---:|---:|---:|---:|---:|
| 20 | 244.1 | reno | 95.55 ± 0.08 | 99.9% | 20.21 | 130.7 | 53.6% | 0.0 |
| 20 | 244.1 | cubic | 95.64 ± 0.00 | 100.0% | 20.21 | 177.3 | 72.6% | 0.0 |
| 20 | 244.1 | bbr | 94.54 ± 0.00 | 98.8% | 20.21 | 41.3 | 16.9% | 0.0 |
| 50 | 610.4 | reno | 95.62 ± 0.08 | 100.0% | 50.21 | 230.1 | 37.7% | 0.0 |
| 50 | 610.4 | cubic | 95.64 ± 0.01 | 100.0% | 50.21 | 435.2 | 71.3% | 0.0 |
| 50 | 610.4 | bbr | 94.79 ± 1.82 | 99.1% | 50.21 | 42.3 | 6.9% | 0.0 |
| 100 | 1220.7 | reno | 95.53 ± 0.37 | 99.9% | 100.21 | 169.6 | 13.9% | 0.0 |
| 100 | 1220.7 | cubic | 95.29 ± 0.62 | 99.6% | 100.21 | 403.8 | 33.1% | 0.0 |
| 100 | 1220.7 | bbr | 93.78 ± 0.08 | 98.1% | 100.21 | 80.0 | 6.6% | 0.0 |
