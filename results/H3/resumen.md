# Resumen Estadistico - Experimento H3 (M/G/1-PS)

## 1. Calibracion Base y Validacion Simulador PS
- **RTT real medido (ping):** `0.199 ms`
- **Servicio vacio PS (s0):** `0.0052 ms`
- **Latencia externa base (c0):** `1.6577 ms`
- **Validacion PS M/M/1 (rho=0.7):** Teo=`33.33 ms`, Sim=`33.70 ms` (`+1.10%`)
- **Validacion PS M/H2/1 (rho=0.7):** Teo=`33.33 ms`, Sim=`34.21 ms` (`+2.63%`)

## 2. Insensibilidad de E[T] y Descomposicion Handler vs. Total
| Dist | rho_obj | rho_med | rho_cg | E[T] Total Med +- IC95% (ms) | E[T] Sim PS (ms) | E[T] Teo PS (ms) | T_handler Med (ms) | T_handler Teo (ms) | T_externo Med (ms) |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| det | 0.5 | 0.504 | 0.578 | 27.67 +- 2.70 | 22.05 | 21.82 | 19.82 | 20.16 | 7.84 |
| exp | 0.5 | 0.489 | 0.560 | 26.84 +- 5.37 | 20.94 | 20.87 | 19.24 | 19.22 | 7.58 |
| h2 | 0.5 | 0.496 | 0.567 | 27.70 +- 4.47 | 20.88 | 21.24 | 19.82 | 19.58 | 7.86 |
| det | 0.7 | 0.709 | 0.787 | 49.09 +- 7.88 | 36.67 | 36.09 | 32.17 | 34.43 | 16.93 |
| exp | 0.7 | 0.710 | 0.788 | 56.88 +- 13.40 | 36.19 | 36.35 | 34.91 | 34.69 | 21.93 |
| h2 | 0.7 | 0.697 | 0.777 | 62.03 +- 32.72 | 34.26 | 34.64 | 34.72 | 32.98 | 27.30 |
| det | 0.9 | 0.897 | 0.965 | 278.76 +- 349.98 | 98.66 | 98.84 | 97.60 | 97.18 | 181.14 |
| exp | 0.9 | 0.900 | 0.969 | 911.46 +- 1800.64 | 108.69 | 103.07 | 122.74 | 101.41 | 788.84 |
| h2 | 0.9 | 0.897 | 0.968 | 644.30 +- 554.58 | 84.37 | 100.26 | 102.18 | 98.60 | 541.73 |

## 3. Pendiente Empirica E[T | S=s] vs. Teorica 1/(1 - rho)
| rho_obj | rho_med | Pendiente Teorica 1/(1-rho) | Pendiente Total Medida | Pendiente Handler Medida | KS D_stat | Atraso envio (ms) | Autocorr r1 |
|:---|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 0.496 | 1.99 | 2.20 | 2.19 | 0.0156 | 0.259 | 0.467 |
| 0.7 | 0.705 | 3.39 | 3.82 | 3.81 | 0.0169 | 0.203 | 0.591 |
| 0.9 | 0.898 | 9.78 | 9.41 | 9.74 | 0.0213 | 0.158 | 0.915 |
