# Trabajo Final — Tráfico (2026)

Plataforma reproducible de emulación de red y medición experimental sobre contenedores Linux (`Docker Compose`, `tc netem`, `tc tbf`, `cgroups v2` y `HAProxy`) para contrastar tráfico real frente a modelos analíticos y simulaciones de traza:

* **Parte 1 — Control de congestión TCP (`T1`–`T5`):** Calibración de enlace/RTT/jitter/pérdida, límite por ventana y BDP, dimensionamiento de buffer ($1$ y $N$ flujos), pérdida aleatoria y equidad (índice de Jain, sesgo por RTT y competencia entre Reno, CUBIC y BBR v1).

* **Parte 2 — Colas HTTP y balanceo (`H1`–`H5`):** Caracterización de servicio FastAPI con demanda exacta de CPU y throttling CFS, cola $M/G/1$ FIFO (Pollaczek–Khinchine y Lindley), $M/G/1$-PS (insensibilidad y contención del GIL), tándem CPU $\rightarrow$ enlace (Redes de Jackson) y balanceo con HAProxy (`random`, `roundrobin`, `random(2)`, `leastconn` y cola central `maxconn 1`).

## 1. Estructura del repositorio

```
.
├── app/                         # Aplicación HTTP (FastAPI) y generador de carga de lazo abierto
│   ├── servicio.py              # Endpoint GET /t?d=D&r=R (modos FIFO async y PS threadpool)
│   └── generador.py             # Cliente Poisson asyncio + aiohttp con espera híbrida
├── config/                      # Scripts de configuración de red y sondas dentro de contenedores
│   ├── nodo.sh                  # Quita ruta por defecto y desactiva GRO/GSO/TSO (ethtool -K)
│   ├── tc.sh                    # Configura tc netem (r_wan) y tc tbf drop-tail (r_bn)
│   ├── tc5.sh                   # Configura RTT diferenciados por puerto (prio + u32) para T5(b)
│   ├── sonda.py                 # Sonda UDP/TCP para calibración de RTT, jitter y pérdida (T1)
│   ├── muestreo.py              # Muestreo periódico (100 ms) de ocupación de cola en tc tbf
│   └── muestreo_ss.py           # Muestreo de sockets TCP (snd_wnd, cwnd, srtt, retrans) con ss
├── haproxy/
│   └── haproxy.cfg              # Configuración dinámica de balanceo para H5
├── results/                     # Datos crudos, tablas resumen (.md) y gráficos (.png)
│   ├── t1-capacidad/ ... t5-algos/   # Resultados de la Parte 1 (TCP)
│   └── p2/                      # Resultados de la Parte 2 (H1..H5)
├── graficas_comparativas/       # Comparación H5 (capacidad fija vs. escalamiento clásico)
├── compose.yaml                 # Topología Docker Compose para Parte 1 (T1..T5)
├── compose-p2.yaml              # Topología Docker Compose para Parte 2 (H1..H4)
├── compose-h5.yaml              # Topología Docker Compose para Parte 2 (H5 con HAProxy y K=4)
├── Dockerfile                   # Imagen base de los nodos (Python 3.12, iproute2, iperf3, etc.)
├── requirements.txt             # Dependencias Python fijadas
├── .env                         # Asignación de núcleos (cpuset) para Parte 1
├── modelos.py                   # Fórmulas cerradas y modelo de fluido numérico TCP
├── correr.py                    # Runner de experimentos de la Parte 1 (T1..T5)
├── analizar.py                  # Análisis estadístico y gráficos de la Parte 1
├── correr_h.py                  # Runner de experimentos de la Parte 2 (H1..H5)
├── analizar_h.py                # Análisis estadístico, simuladores y gráficos de la Parte 2
├── analizar_h_comparativo.py    # Análisis comparativo complementario de la Parte 2
├── analizar_h5_escalamiento.py  # Contraste H5: capacidad fija vs. escalamiento horizontal clásico
├── INFORME.md                   # Informe final completo con metodología, tablas y discusiones
└── README.md                    # Esta guía de reproducción


```

## 2. Requisitos y preparación del host

### 2.1 Requisitos de sistema

* Linux con **cgroups v2** habilitado (`/sys/fs/cgroup/cgroup.controllers`).

* Al menos **4 núcleos físicos** (8 hilos SMT; los núcleos `0..3` se asignan a host y contenedores, dejando `4..7` libres para evitar contención SMT) y **8 GB de RAM**.

* **Docker Engine** ($\ge 24.0$, compatible con Docker $\ge 28$ gracias a `gateway_mode_ipv4: nat-unprotected`) y **Docker Compose v2** (`docker compose`).

* **Python 3.11+** en el host.

### 2.2 Configuración del kernel y CPU en el host

Ejecutar en el host antes de correr los experimentos para habilitar BBR, `sch_netem`/`sch_tbf`, buffers de socket amplios y frecuencia fija sin *turbo boost*:

```
# 1. Cargar módulos de control de congestión y colas
sudo modprobe tcp_bbr
sudo modprobe sch_netem
sudo modprobe sch_tbf

# 2. Aumentar límites de buffers de socket en el host
sudo sysctl -w net.core.rmem_max=16777216
sudo sysctl -w net.core.wmem_max=16777216

# 3. Fijar gobernador de frecuencia en performance y desactivar turbo (opcional pero recomendado)
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [ -f "$gov" ] && echo performance | sudo tee "$gov" > /dev/null
done
if [ -f /sys/devices/system/cpu/intel_pstate/no_turbo ]; then
    echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo > /dev/null
fi


```

### 2.3 Entorno virtual Python y archivo `.env`

```
# Crear y activar entorno virtual
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Verificar/crear el archivo .env con la asignación de núcleos para Parte 1
cat << 'EOF' > .env
CPU_CLI=1
CPU_SRV=2
CPU_ROUTERS=3
EOF


```

### 2.4 Construcción de la imagen Docker

```
docker compose -f compose.yaml build


```

## 3. Reproducción de la Parte 1: TCP (`T1`–`T5`)

### 3.1 Levantar la infraestructura de red (Parte 1)

```
docker compose -f compose.yaml up -d


```

Esto crea las redes `net_acc` (`198.18.1.0/24`), `net_core` (`198.18.2.0/24`) y `net_srv` (`198.18.3.0/24`) y levanta los contenedores `cli`, `r_wan`, `r_bn` y `srv` con `GRO/GSO/TSO` desactivados y sin salida a Internet.

### 3.2 Ejecución de corridas (`correr.py`)

El runner guarda cada corrida en `results/<serie>/<corrida>/` con `meta.json`, `iperf.json` y trazas auxiliares (`cola.txt`, `ss.txt`), salteando automáticamente corridas ya completadas (`status == "ok"`):

```
# Ejecutar todas las series de la Parte 1 (T1 a T5)
python3 correr.py todo

# O ejecutar por experimento individual:
python3 correr.py t1-capacidad
python3 correr.py t1-rtt
python3 correr.py t1-perdida
python3 correr.py t2-ventana
python3 correr.py t2-libre
python3 correr.py t3-uno
python3 correr.py t3-n
python3 correr.py t4-perdida
python3 correr.py t5-n
python3 correr.py t5-rtt
python3 correr.py t5-algos


```

> **Nota:** Para verificar la cadena completa en pocos minutos antes de lanzar las repeticiones largas, puede usarse el modo rápido si está disponible o inspeccionar `python3 correr.py --help`.

### 3.3 Análisis estadístico y generación de tablas/gráficos (`analizar.py`)

Ejecutar siempre fijado al **núcleo `0`** (reservado al host) para no interferir con los núcleos medidos:

```
taskset -c 0 python3 analizar.py todo


```

Esto genera para cada experimento un archivo `results/<serie>/resumen.md` con todas las tablas, intervalos de confianza y pruebas estadísticas, junto con las figuras `.png` correspondientes.

### 3.4 Apagar topología de la Parte 1

```
docker compose -f compose.yaml down


```

## 4. Reproducción de la Parte 2: Colas HTTP y Balanceo (`H1`–`H5`)

En la Parte 2 todos los contenedores emplean `cpu_period: 10000` ($10\text{ ms}$) para evitar bloqueos largos del planificador CFS bajo cuotas fraccionarias.

### 4.1 Experimentos `H1` a `H4` (servidor único FastAPI)

1. **Levantar la topología (`compose-p2.yaml`):**

   ```
   docker compose -f compose-p2.yaml up -d
   
   
   ```

2. **Ejecutar las series `H1`, `H2`, `H3` y `H4`:**

   ```
   python3 correr_h.py H1
   python3 correr_h.py H2
   python3 correr_h.py H3
   python3 correr_h.py H4
   
   
   ```

   Los datos crudos (`meta.json`, `pedidos.csv` con tiempos programados, de envío real, entrada/salida de handler, CPU consumida y estadísticas de `cgroup cpu.stat`) se almacenan en `results/p2/<EXP>/<run_id>/`.

3. **Bajar la topología `compose-p2.yaml`:**

   ```
   docker compose -f compose-p2.yaml down
   
   
   ```

### 4.2 Experimento `H5` (balanceo de carga con HAProxy)

1. **Levantar la topología con `HAProxy` y 4 backends (`compose-h5.yaml`):**

   ```
   docker compose -f compose-h5.yaml up -d
   
   
   ```

   *(Para* $K=2$*, se utilizan `api1` y `api2` al* $100\%$ *de cuota en núcleos separados con* $E[D]=20\text{ ms}$*; para* $K=4$*, operan `api1..api4` en parejas sobre 2 núcleos con cuota estricta del* $50\%$ *y* $E[D]=20\text{ ms}$*, resultando en* $E[S_{\text{ef}}]=40\text{ ms}$ *y manteniendo fija la capacidad agregada de cómputo).*

2. **Ejecutar las corridas de `H5` (**$K \in \{2, 4\}$**,** $\rho \in \{0.6, 0.9\}$**, 5 políticas):**

   ```
   python3 correr_h.py H5
   
   
   ```

3. **Bajar la topología `compose-h5.yaml`:**

   ```
   docker compose -f compose-h5.yaml down
   
   
   ```

### 4.3 Análisis estadístico, simuladores de traza y gráficos (`analizar_h*.py`)

Ejecutar los scripts de análisis fijados al **núcleo `0`**:

```
# 1. Análisis principal H1..H5 (tablas resumen.md, Lindley, PS, Jackson y HAProxy)
taskset -c 0 python3 analizar_h.py

# 2. Gráficos comparativos detallados por hipótesis
taskset -c 0 python3 analizar_h_comparativo.py

# 3. Análisis comparativo H5: capacidad fija vs. escalamiento horizontal clásico
taskset -c 0 python3 analizar_h5_escalamiento.py


```

Las salidas se escriben en:

* `results/p2/resumen.md` y `results/p2/h1_*.png` .. `results/p2/h5_*.png`

* `graficas_comparativas/analisis_escalamiento_h5.md` y `graficas_comparativas/h5_comparacion_escalamiento_clasico.png`

## 5. Verificación rápida de integridad de resultados

Para comprobar que todas las corridas guardadas están completas y sin errores HTTP ni fallos de ejecución:

```
python3 -c '
import json, pathlib
p1 = [json.loads(p.read_text()) for p in pathlib.Path("results").rglob("meta.json") if "p2" not in p.parts]
p2 = [json.loads(p.read_text()) for p in pathlib.Path("results/p2").rglob("meta.json")]
ok1 = sum(1 for m in p1 if m.get("status") == "ok")
ok2 = sum(1 for m in p2 if json.loads(m.get("generador_stdout") or "{}").get("pedidos_medicion_error", 0) == 0)
print(f"Parte 1 (TCP):  {ok1}/{len(p1)} corridas válidas")
print(f"Parte 2 (HTTP): {ok2}/{len(p2)} corridas válidas")
'


```