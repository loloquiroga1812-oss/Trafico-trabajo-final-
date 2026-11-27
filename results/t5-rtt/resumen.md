# T5(b) — Dos flujos con RTT distinto: exponente de sesgo por RTT

Un flujo con RTT = 20 ms y otro con RTT₂ (20 ms = **control**: debe repartir parejo, y valida el armado con `tc5.sh`). Dos clientes `iperf3` en puertos distintos; en las repeticiones pares se intercambian los puertos. Buffer = BDP de 20 ms. Razón x₁/x₂ = goodput del flujo corto / goodput del largo, y se ajusta ln(x₁/x₂) = a·ln(RTT₂/RTT₁) + c con todas las corridas (el control aporta puntos en ln = 0).

**Teoría.** Reno: con pérdidas sincronizadas, Mathis da x₁/x₂ = RTT₂/RTT₁, o sea **a = 1**. CUBIC: fuera de la región amigable con Reno su ventana crece en tiempo real y no en RTTs, así que dos flujos con distinto RTT tienden a ventanas parecidas y un cociente de goodput lineal en el RTT (también a = 1; ver el principio de diseño en el RFC 9438). Con pérdida aleatoria de tasa fija, en cambio, la respuesta de CUBIC da x ∝ RTT^(−1/4) (a = 1/4, T4); aquí el desborde del buffer es compartido, no aleatorio. BBR no tiene fórmula cerrada simple (ver Hock et al. 2017 y Ware et al. 2019 de la bibliografía): se reporta lo medido.

## Razón de goodput por condición

| Algoritmo | RTT₂ (ms) | n | x₁/x₂ medida | ± IC95 | Si a = 1 | Si a = 1/4 | Exponente por condición | ± IC95 | Jain | Utilización (%) | ± IC95 | Cola media / BDP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bbr | 20 | 3 | 0.962 | 0.092 | 1.00 | 1.00 | — | — | 0.9994 | 98.22 | 0.15 | 0.893 |
| bbr | 40 | 3 | 1.162 | 0.146 | 2.00 | 1.19 | 0.216 | 0.180 | 0.9941 | 98.24 | 0.17 | 0.924 |
| bbr | 80 | 3 | 1.080 | 0.111 | 4.00 | 1.41 | 0.055 | 0.075 | 0.9983 | 98.66 | 0.16 | 0.909 |
| bbr | 160 | 3 | 1.263 | 0.178 | 8.00 | 1.68 | 0.112 | 0.067 | 0.9864 | 97.61 | 3.91 | 0.822 |
| cubic | 20 | 3 | 1.049 | 0.346 | 1.00 | 1.00 | — | — | 0.9965 | 100.01 | 0.00 | 0.802 |
| cubic | 40 | 3 | 2.161 | 1.040 | 2.00 | 1.19 | 1.092 | 0.737 | 0.8831 | 100.01 | 0.05 | 0.761 |
| cubic | 80 | 3 | 3.148 | 1.234 | 4.00 | 1.41 | 0.821 | 0.287 | 0.7913 | 100.06 | 0.03 | 0.728 |
| cubic | 160 | 3 | 1.951 | 1.501 | 8.00 | 1.68 | 0.305 | 0.387 | 0.9079 | 100.30 | 0.25 | 0.685 |
| reno | 20 | 3 | 1.054 | 0.218 | 1.00 | 1.00 | — | — | 0.9982 | 99.98 | 0.04 | 0.617 |
| reno | 40 | 3 | 2.183 | 0.897 | 2.00 | 1.19 | 1.112 | 0.616 | 0.8803 | 99.67 | 0.05 | 0.538 |
| reno | 80 | 3 | 3.977 | 0.471 | 4.00 | 1.41 | 0.995 | 0.086 | 0.7367 | 99.70 | 0.03 | 0.523 |
| reno | 160 | 3 | 8.452 | 5.072 | 8.00 | 1.68 | 1.018 | 0.274 | 0.6205 | 99.84 | 0.25 | 0.508 |

## Verificación del RTT por flujo (srtt del emisor, ss)

El srtt de cada flujo incluye la cola del cuello de botella, que es **común** a los dos. Por eso se comparan dos cosas: la *diferencia* de srtt entre el flujo largo y el corto, que debe coincidir con RTT₂ − RTT₁, y la *cola* de cada flujo (srtt − RTT configurado − 0.33 ms de base), que debe ser parecida entre los dos. Si el filtro u32 no separara bien los flujos, la diferencia no coincidiría.

| Algoritmo | RTT₂ (ms) | srtt flujo corto (ms) | srtt flujo largo (ms) | srtt largo − corto (ms) | RTT₂ − RTT₁ configurado (ms) | Cola del flujo corto (ms) | Cola del flujo largo (ms) |
|---|---|---|---|---|---|---|---|
| bbr | 20 | 38.47 | 38.46 | -0.01 | 0.0 | 18.14 | 18.13 |
| bbr | 40 | 39.06 | 59.15 | 20.09 | 20.0 | 18.73 | 18.82 |
| bbr | 80 | 38.82 | 99.08 | 60.25 | 60.0 | 18.49 | 18.75 |
| bbr | 160 | 36.94 | 177.87 | 140.93 | 140.0 | 16.61 | 17.54 |
| cubic | 20 | 36.17 | 36.17 | 0.00 | 0.0 | 15.84 | 15.84 |
| cubic | 40 | 35.32 | 55.37 | 20.04 | 20.0 | 14.99 | 15.04 |
| cubic | 80 | 34.67 | 94.68 | 60.01 | 60.0 | 14.34 | 14.35 |
| cubic | 160 | 33.86 | 173.79 | 139.94 | 140.0 | 13.53 | 13.46 |
| reno | 20 | 32.51 | 32.42 | -0.09 | 0.0 | 12.18 | 12.09 |
| reno | 40 | 30.86 | 50.88 | 20.02 | 20.0 | 10.53 | 10.55 |
| reno | 80 | 30.61 | 90.65 | 60.04 | 60.0 | 10.28 | 10.32 |
| reno | 160 | 30.35 | 170.30 | 139.96 | 140.0 | 10.02 | 9.97 |

## Exponente a por regresión

| Algoritmo | Corridas | Exponente a | ± IC95 | p (H₀: a = 1) | p (H₀: a = 1/4) | Intercepto c | ± IC95 | p (H₀: c = 0) | R² |
|---|---|---|---|---|---|---|---|---|---|
| bbr | 12 | 0.107 | 0.061 | <0.001 | <0.001 | -0.007 | 0.080 | 0.853 | 0.602 |
| cubic | 12 | 0.311 | 0.326 | <0.001 | 0.684 | 0.319 | 0.423 | 0.123 | 0.311 |
| reno | 12 | 0.982 | 0.115 | 0.733 | <0.001 | 0.059 | 0.149 | 0.402 | 0.973 |

a > 0: gana el flujo de RTT corto; a < 0: gana el de RTT largo. El intercepto c ≠ 0 indicaría un sesgo que no es del RTT (puerto, orden de arranque). Un p chico no implica un desvío importante: mirar a, su IC y el control.

### Chequeo de CPU

Governor del host: performance; no_turbo: [1]. 

Frecuencia (MHz) de los CPUs medidos, muestreada cada 1 s durante la medición:

| CPU | Mín | Media | Máx |
|---|---|---|---|
| 0 | 392 | 1209 | 2819 |
| 1 | 400 | 2588 | 2802 |
| 2 | 394 | 1791 | 2808 |
| 3 | 392 | 1058 | 2809 |

Uso de CPU del cgroup de cada contenedor (% de un núcleo durante la medición):

| Contenedor | Media % | Máx % |
|---|---|---|
| cli | 7.3 | 11.6 |
| r_bn | 4.5 | 4.9 |
| r_wan | 2.6 | 2.7 |
| srv | 9.1 | 11.3 |
