# Resumen estadistico - T1 (RTT y jitter), flujo srv -> cli

- **RTT base (sin netem):** 0.1723 ms (+- 0.0014 IC95% entre 3 repeticiones de 500 pings)
- Se descartan los primeros 10 pings de cada corrida.
- Modelo: RTT = RTT_cfg + RTT_base. netem aplica RTT_cfg/2 en cada sentido; con jitter J cada sentido suma una normal de desvio J, asi que sigma_RTT = J*sqrt(2).
- Media y sigma por corrida salen del resumen de `ping` (precision completa). Las muestras individuales de ping tienen 3 cifras significativas, por eso para los tests de forma se les suma ruido uniforme de +-paso/2 (des-cuantizacion).

## 1. Barrido de RTT (jitter = 0)

| RTT_cfg (ms) | Teorico (ms) | Medido +- IC95% (ms) | Error (ms) | Error (%) | p (t 1 muestra) | sigma_emp (ms) |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0.172 | 0.173 +- 0.001 | +0.000 | +0.19% | 0.423 | 0.014 |
| 10 | 10.172 | 10.766 +- 0.022 | +0.593 | +5.83% | 7.61e-05 | 0.145 |
| 50 | 50.172 | 50.701 +- 0.011 | +0.528 | +1.05% | 2.43e-05 | 0.140 |
| 100 | 100.172 | 100.634 +- 0.036 | +0.461 | +0.46% | 0.000336 | 0.156 |
| 200 | 200.172 | 200.665 +- 0.020 | +0.492 | +0.25% | 8.98e-05 | 0.176 |

**Regresion** RTT_medido = a + b*RTT_cfg (J=0, 15 corridas): pendiente b = 1.00103 +- 0.00163 (IC95%), intercepto a = 0.5132 +- 0.1668 ms (RTT base medido: 0.1723 ms), R^2 = 0.999993.
- Pendiente = 1 dentro del IC: **si**; intercepto = base dentro del IC: **no**.
- Con varianza casi nula entre repeticiones el t de una muestra detecta cualquier sesgo: importa el error en % y en ms, no solo p.

## 2. Jitter a RTT = 50 ms

| J (ms) | Teorico (ms) | Teorico con truncamiento (ms) | Medido +- IC95% (ms) | Error (%) | p (t 1 muestra) |
|---:|---:|---:|---:|---:|---:|
| 0 | 50.172 | 50.172 | 50.701 +- 0.011 | +1.05% | 2.43e-05 |
| 2 | 50.172 | 50.180 | 50.576 +- 0.399 | +0.81% | 0.0488 |
| 10 | 50.172 | 50.251 | 50.881 +- 0.949 | +1.41% | 0.0847 |

### Desvio del RTT

| J (ms) | sigma_teo = J*sqrt2 (ms) | sigma con truncamiento (ms) | sigma_emp +- IC95% (ms) | sigma_emp / sigma_teo | autocorr. lag 1 |
|---:|---:|---:|---:|---:|---:|
| 0 | 0.000 | 0.000 | 0.140 +- 0.009 | - | +0.205 |
| 2 | 2.828 | 2.824 | 2.796 +- 0.239 | 0.989 | +0.010 |
| 10 | 14.142 | 14.047 | 14.258 +- 1.441 | 1.008 | -0.066 |

- sigma_emp es el `mdev` de ping promediado entre repeticiones, con IC t de Student. Si el cociente da ~1 el RTT tiene desvio J*sqrt(2) (dos sentidos independientes), no J.
- Autocorrelacion lag 1 ~ 0 justifica tratar los pings como muestras independientes.

### Forma de la distribucion (muestras agrupadas)

| J (ms) | n | KS vs N(mu_teo, sigma_teo) | Lilliefors | Shapiro-Wilk | asimetria | curtosis exc. | p Kruskal entre reps |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 1470 | 0.0774 (p=4.25e-08) | nan (p=nan) | 0.9979 (p=0.0573) | +0.043 | +0.194 | 0.102 |
| 10 | 1470 | 0.0430 (p=0.00854) | nan (p=nan) | 0.9988 (p=0.423) | -0.011 | -0.063 | 0.855 |

- KS usa parametros teoricos (no estimados de la muestra), asi que evalua forma + media + desvio juntos. Lilliefors y Shapiro evaluan solo la forma (normalidad) con parametros estimados.
- Con ~1500 muestras estos tests detectan desvios minimos (tabla de la normal de netem discretizada, truncamiento en 0 y +-4 sigma): mirar el estadistico D, la asimetria/curtosis y el Q-Q plot, no solo p.
- Un p alto en Kruskal entre repeticiones indica que las repeticiones son consistentes entre si.
