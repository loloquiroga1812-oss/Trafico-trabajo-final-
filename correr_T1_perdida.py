import subprocess
import json
import random
import time
from datetime import datetime
from pathlib import Path
import itertools

EXPERIMENTO = "T1-Perdida"
DIR_BASE = Path("results") / EXPERIMENTO

def ejecutar(comando, ignorar_error=False):
    res = subprocess.run(comando, shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not ignorar_error:
        print(f"Error en comando: {comando}\nSalida: {res.stderr}")
    return res.stdout

def asegurar_python(contenedor):
    """Verifica si python3 está instalado en el contenedor; si no, lo instala según la distro."""
    res = subprocess.run(f"docker exec {contenedor} which python3", shell=True, capture_output=True)
    if res.returncode == 0:
        return
    print(f"[{contenedor}] 'python3' no encontrado. Instalándolo en el contenedor...")
    # Intentar con Alpine (apk)
    res_apk = subprocess.run(f"docker exec {contenedor} apk add --no-cache python3", shell=True, capture_output=True)
    if res_apk.returncode == 0:
        return
    # Intentar con Debian/Ubuntu (apt-get)
    subprocess.run(f"docker exec {contenedor} apt-get update && docker exec {contenedor} apt-get install -y python3", shell=True, capture_output=True)

def limpiar_tc():
    ejecutar("docker exec r_bn tc qdisc del dev eth0 root", True)
    ejecutar("docker exec r_wan tc qdisc del dev eth0 root", True)
    ejecutar("docker exec r_wan tc qdisc del dev eth1 root", True)

def configurar_tc_perdida(p_loss):
    limpiar_tc()
    if p_loss > 0:
        cmd = f"tc qdisc add dev eth1 root netem loss {p_loss}%"
        ejecutar(f"docker exec r_wan {cmd}")

def ejecutar_prueba_udp(total_paquetes=3000, tasa_pps=200):
    # Asegurar que python3 esté disponible en ambos contenedores
    asegurar_python("srv")
    asegurar_python("cli")

    script_servidor = (
        "python3 -c \""
        "import socket, json\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "s.bind(('0.0.0.0', 5000))\n"
        "s.settimeout(3.0)\n"
        "recibidos = []\n"
        "while True:\n"
        "    try:\n"
        "        data, _ = s.recvfrom(1024)\n"
        "        recibidos.append(int(data.decode().strip()))\n"
        "    except socket.timeout:\n"
        "        break\n"
        "    except Exception:\n"
        "        break\n"
        "s.close()\n"
        "print(json.dumps(recibidos))\n"
        "\""
    )
    
    proc_srv = subprocess.Popen(
        f"docker exec srv {script_servidor}", 
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    
    time.sleep(0.5)

    script_cliente = (
        "python3 -c \""
        "import socket, time\n"
        f"s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        f"total = {total_paquetes}\n"
        f"intervalo = 1.0 / {tasa_pps}\n"
        "for i in range(total):\n"
        "    s.sendto(str(i).encode(), ('198.18.3.10', 5000))\n"
        "    time.sleep(intervalo)\n"
        "s.close()\n"
        "\""
    )
    ejecutar(f"docker exec cli {script_cliente}")

    stdout, _ = proc_srv.communicate()
    try:
        lineas = stdout.strip().split('\n')
        recibidos = json.loads(lineas[-1])
    except Exception as e:
        print(f"Error al parsear paquetes recibidos: {e} | Salida: {stdout}")
        recibidos = []

    return recibidos

def main():
    print(f"--- Iniciando Experimento: {EXPERIMENTO} ---")
    DIR_BASE.mkdir(parents=True, exist_ok=True)

    losses = [0.1, 1.0, 5.0]
    repeticiones = [1, 2, 3]

    corridas = list(itertools.product(losses, repeticiones))
    random.shuffle(corridas)

    total_paquetes = 3000

    for p_loss, rep in corridas:
        nombre = f"Loss{p_loss}_rep{rep}"
        dir_corrida = DIR_BASE / nombre
        archivo_meta = dir_corrida / "meta.json"
        
        if archivo_meta.exists():
            print(f"[{nombre}] Ya analizado, salteando...")
            continue
            
        print(f"[{nombre}] Configurando tc loss={p_loss}% y enviando {total_paquetes} paquetes UDP...")
        dir_corrida.mkdir(parents=True, exist_ok=True)
        
        try:
            configurar_tc_perdida(p_loss)
            time.sleep(0.5)
            
            recibidos = ejecutar_prueba_udp(total_paquetes=total_paquetes)
            
            data_guardar = {
                "total_enviados": total_paquetes,
                "recibidos": recibidos
            }
            with open(dir_corrida / "traza.json", "w") as f:
                json.dump(data_guardar, f)
                
            meta = {
                "experimento": EXPERIMENTO,
                "parametros": {"loss_configurado_pct": p_loss},
                "repeticion": rep,
                "hora": datetime.now().isoformat()
            }
            with open(archivo_meta, "w") as f:
                json.dump(meta, f, indent=4)
                
        except Exception as e:
            print(f"[{nombre}] ERROR: {e}")

    limpiar_tc()
    print("--- Barrido de Pérdidas finalizado ---")

if __name__ == "__main__":
    main()
