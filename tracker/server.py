import os
import http.server
import socketserver
import subprocess
import threading

def initiate(file_to_run):
    # Get the full path of the manager script
    file_path = os.path.join(os.path.dirname(__file__), file_to_run)
    subprocess.call(["python", file_path])

start = threading.Thread(target=initiate, args=("manager.py",))
start.start()

PORT = 8000  # Port number to use

Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print("Server running on localhost port", PORT)
    httpd.serve_forever()
