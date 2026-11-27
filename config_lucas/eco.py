#!/usr/bin/env python3
"""Servidor de eco UDP (corre en srv). Devuelve cada datagrama tal cual."""
import socket, sys

puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
s.bind(("0.0.0.0", puerto))
while True:
    datos, origen = s.recvfrom(2048)
    s.sendto(datos, origen)
