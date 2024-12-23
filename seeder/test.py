import requests

server = 'http://192.168.1.9:8000'
tracker_url = f"{server}/tracker.txt"
try:
    response = requests.get(tracker_url)
    response.raise_for_status()
    tracker_ip, tracker_port = response.text.strip().split()
    print(tracker_ip, int(tracker_port))
except requests.RequestException as e:
    print(f"Failed to retrieve tracker information: {e}")