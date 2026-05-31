import socket

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", 9999))
s.settimeout(10)
print("Escuchando en 127.0.0.1:9999 ...")
print("Esperando datos de ATAS...\n")

while True:
    try:
        data, addr = s.recvfrom(65535)
        print(f"RECIBIDO desde {addr}: {data.decode()[:120]}")
    except socket.timeout:
        print("... sin datos (10s)")