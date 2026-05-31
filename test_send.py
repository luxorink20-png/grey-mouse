import socket
import time

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

for i in range(5):
    msg = f"5234.25,5234.00,5235.00,5233.50,5234.25,1000,50,525,475,0,1234567890,MESM6,{i}"
    s.sendto(msg.encode(), ("127.0.0.1", 9999))
    print(f"Enviado tick {i}: {msg[:60]}")
    time.sleep(1)

print("\nDone.")