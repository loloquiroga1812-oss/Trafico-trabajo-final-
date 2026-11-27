import subprocess
import json
import random
import zlib
from datetime import datetime
from pathlib import Path
import itertools

# Especificamos claramente de qué experimento se trata
EXPERIMENTO = "T1-Capacidad"
DIR_BASE = Path("results") / EXPERIMENTO

def ejecutar(comando, ignorar_error=False):
    """Ejecuta un comando en la terminal y devuelve la salida."""
    res = subprocess.run(comando, shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not ignorar_error:
        raise Exception(f"Error en comando: {comando}\nSalida: {res.stderr}")
    return res.stdout

def configurar_tc(capacidad_mbit):
    """Limpia reglas previas y configura el cuello de botella (r_bn) y retardo (r_wan)."""
    # 1. Limpiar tc previo (ignoramos error por si no existían reglas)
    ejecutar("docker exec r_bn tc qdisc del dev eth0 root", ignorar_error=True)
    ejecutar("docker exec r_wan tc qdisc del dev eth0 root", ignorar_error=True)
    ejecutar("docker exec r_wan tc qdisc del dev eth1 root", ignorar_error=True)
    
    # 2. Configurar r_bn: Capacidad C y buffer holgado (para calibrar sin descartes)
    burst = 15140 # 10 tramas aprox
    buffer_holgado = 1000000 # 1 MB (holgado para que no influya en T1)
    comando_tbf = f"tc qdisc add dev eth0 root tbf rate {capacidad_mbit}mbit burst {burst} limit {buffer_holgado}"
    ejecutar(f"docker exec r_bn {comando_tbf}")
    
    # 3. Configurar r_wan: RTT chico y límite holgado para que netem no descarte paquetes
    # Aplicamos 1ms por sentido = 2ms de RTT extra
    comando_netem0 = "tc qdisc add dev eth0 root netem delay 1ms limit 100000"
    comando_netem1 = "tc qdisc add dev eth1 root netem delay 1ms limit 100000"
    ejecutar(f"docker exec r_wan {comando_netem0}")
    ejecutar(f"docker exec r_wan {comando_netem1}")

def correr_iperf(algoritmo):
    """Ejecuta iperf3 desde el cliente hacia el servidor."""
    # -R (reverso, srv a cli), -C (algoritmo TCP), -O 1 (omite 1er segundo transitorio), -J (salida JSON)
    comando = f"docker exec cli iperf3 -c 198.18.3.10 -R -C {algoritmo} -O 1 -t 10 -J"
    return ejecutar(comando)

def main():
    print(f"--- Iniciando Experimento: {EXPERIMENTO} ---")
    DIR_BASE.mkdir(parents=True, exist_ok=True)

    # Dejamos corriendo el servidor iperf3 en modo daemon (-s -D) dentro de srv
    ejecutar("docker exec srv iperf3 -s -D", ignorar_error=True)

    # Definimos el espacio del experimento T1 (Capacidad)
    capacidades = [10, 25, 50, 100]
    algoritmos = ["reno", "cubic", "bbr"]
    repeticiones = [1, 2, 3]

    # Generamos todas las combinaciones posibles
    corridas = list(itertools.product(capacidades, algoritmos, repeticiones))
    random.shuffle(corridas) # Regla de diseño: Orden aleatorio

    for C, alg, rep in corridas:
        nombre_corrida = f"C{C}_{alg}_rep{rep}"
        dir_corrida = DIR_BASE / nombre_corrida
        archivo_meta = dir_corrida / "meta.json"
        
        # Buena práctica 1: Saltear si ya tiene meta.json
        if archivo_meta.exists():
            print(f"[{nombre_corrida}] Ya analizado previamente, salteando...")
            continue
            
        print(f"[{nombre_corrida}] Ejecutando medición...")
        dir_corrida.mkdir(parents=True, exist_ok=True)
        
        # Semilla reproducible derivada del nombre (evitar hash() de Python)
        semilla = zlib.crc32(nombre_corrida.encode())
        
        try:
            # 1. Aplicar las reglas de tráfico
            configurar_tc(C)
            
            # 2. Ejecutar la medición
            salida_json = correr_iperf(alg)
            
            # 3. Guardar datos crudos (Buena práctica 2)
            with open(dir_corrida / "iperf.json", "w") as f:
                f.write(salida_json)
                
            # 4. Guardar metadatos
            meta = {
                "experimento": EXPERIMENTO,
                "parametros": {
                    "capacidad_mbit": C,
                    "algoritmo": alg,
                    "buffer": "holgado"
                },
                "repeticion": rep,
                "semilla": semilla,
                "hora": datetime.now().isoformat(),
                "estado_tc": "OK"
            }
            with open(archivo_meta, "w") as f:
                json.dump(meta, f, indent=4)
                
        except Exception as e:
            # Buena práctica 5: Capturar excepción y no frenar la serie entera
            print(f"[{nombre_corrida}] ERROR: {e}")

    print("--- Barrido T1 finalizado ---")

if __name__ == "__main__":
    main()
