import http.server
import socketserver
import subprocess
import threading

def initiate(file_to_run):
    subprocess.call(["python", file_to_run])

start = threading.Thread(target=initiate, args=("manager.py",))
start.start()

PORT = 8000  # Cổng mà bạn muốn sử dụng

Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print("Server đang chạy tại localhost port", PORT)
    httpd.serve_forever()
