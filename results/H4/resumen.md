# Resumen Estadistico - Experimento H4 (Tandem CPU -> Enlace)

## 1. Calibracion y Validacion del Simulador de Dos Etapas
- **Capacidad enlace cuello de botella (tbf):** `25.0 Mbit/s` (burst `16kb`)
- **Servicio vacio CPU (s0_cpu):** `0.0045 ms`
- **Latencia pura sin cola (c0_puro):** `1.0688 ms`
- **Overhead HTTP+ACKs en cable por pedido (H):** `315 B`
- **Pendiente bytes_cable vs R:** medida `1.0442`, teorica `1.0456`
- **Descartes en buffer tbf (total corridas):** `0`
- **Validacion sintetica Tandem vs. Jackson:** Jackson=`67.86 ms`, Sim=`67.04 ms` (`-1.21%`)

## 2. Utilizaciones (objetivo vs. dos caminos de medicion)
| (rho_cpu, rho_red) | rho_cpu lambda*E[S] | rho_cpu cgroup | rho_red bytes_r (modelo) | rho_red tc (cable) |
|:---|---:|---:|---:|---:|
| (0.5, 0.5) | 0.497 | 0.561 | 0.499 | 0.496 |
| (0.5, 0.7) | 0.507 | 0.569 | 0.707 | 0.707 |
| (0.7, 0.5) | 0.708 | 0.784 | 0.507 | 0.506 |
| (0.7, 0.7) | 0.696 | 0.771 | 0.692 | 0.694 |

## 3. Comparacion Total: Medido vs. Simulacion 2 Etapas vs. Jackson
IC 95% t de Student entre repeticiones (n por combo indicado).

| (rho_cpu, rho_red) | n | E[T] Medido (ms) | E[T] Sim 2 Etapas (ms) | E[T] Jackson (ms) | Error vs Jackson (%) | Error vs Sim (%) |
|:---|---:|---:|---:|---:|---:|---:|
| (0.5, 0.5) | 3 | 39.76 ± 3.60 | 41.20 ± 3.22 | 40.81 | -2.56% | -3.50% |
| (0.5, 0.7) | 3 | 67.92 ± 10.33 | 69.86 ± 10.54 | 69.71 | -2.56% | -2.76% |
| (0.7, 0.5) | 3 | 56.24 ± 8.00 | 49.68 ± 5.11 | 50.06 | +12.34% | +13.19% |
| (0.7, 0.7) | 3 | 71.57 ± 10.46 | 66.15 ± 6.90 | 66.12 | +8.25% | +8.19% |

## 4. Descomposicion por Etapa
Medido: servicio en handler y resto (cola CPU + enlace + red). Sim/Jackson: tiempo en sistema de cada etapa (espera + servicio).

| (rho_cpu, rho_red) | Handler Med (ms) | Resto Med (ms) | Etapa 1 CPU Sim (ms) | Etapa 1 CPU Jackson (ms) | Etapa 2 Enlace Sim (ms) | Etapa 2 Enlace Jackson (ms) |
|:---|---:|---:|---:|---:|---:|---:|
| (0.5, 0.5) | 9.96 | 29.80 | 20.23 | 19.76 | 19.90 | 19.98 |
| (0.5, 0.7) | 10.13 | 57.79 | 20.28 | 20.49 | 48.51 | 48.15 |
| (0.7, 0.5) | 10.05 | 46.19 | 34.25 | 34.42 | 14.37 | 14.57 |
| (0.7, 0.7) | 9.99 | 61.58 | 32.81 | 32.82 | 32.28 | 32.23 |
