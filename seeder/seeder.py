import socket
import threading
import struct
import torrent_file_process
import sys

BITFIELD = 4
BITFIELD_NO_LOOP = 5
REQUEST = 6
PIECE = 7
HAVE = 8

class Seeder:
    def __init__(self, folder_name, piece_length, torrent_file_dest, listen_port=6882, tracker_url = 'http://localhost:8000', print_enabled=False):
        self.folder_name = folder_name
        self.piece_length = piece_length
        self.torrent_file_dest = torrent_file_dest
        self.listen_port = listen_port
        self.listen_ip = socket.gethostbyname(socket.gethostname())
        # self.tracker_ip = tracker_ip
        # self.tracker_port = tracker_port
        self.tracker_url = tracker_url
        self.piece_map = {}  # Mapping from piece index to piece data
        self.create_torrent_file()
        self.tracker_ip, self.tracker_port = torrent_file_process.get_tracker_ip_port(self.tracker_url)
        print(self.tracker_ip, self.tracker_port)
        self.exit_event = threading.Event()  # Used to signal threads to exit
        self.client_sockets = []  # Track active connections to leechers
        self.peer_statistics = {}  # Store statistics for sent/received messages
        self.statistics_lock = threading.Lock()

        self.print_enabled = print_enabled  # Enable/disable detailed logs

    def create_torrent_file(self):
        # Create the torrent file and initialize the piece mapping
        torrent_file_process.create_torrent_file(self.folder_name, self.piece_length, self.torrent_file_dest, self.tracker_url)
        self.piece_map = torrent_file_process.get_piece_map(self.folder_name, self.piece_length)
        self.bitfield = bytearray([1] * len(self.piece_map))  # All pieces are available
    
    def log(self, message):
        if self.print_enabled:
            print(message)

    def register_with_tracker(self):
        try:
            self.tracker_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tracker_socket.connect((self.tracker_ip, self.tracker_port))
            self.tracker_socket.send(str(self.listen_port).encode())
            self.log(f"Registered with tracker at {self.tracker_ip}:{self.tracker_port} on port {self.listen_port}")
        except ConnectionError:
            self.log("Failed to connect to the tracker.")
            self.tracker_socket = None

    def deregister_from_tracker(self):
        # Inform the tracker that this seeder is leaving the swarm
        if self.tracker_socket:
            try:
                self.tracker_socket.send(b"quit")
                print("Informed tracker of seeder's departure.")
            finally:
                self.tracker_socket.close()

    def start_listening(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.bind(('', self.listen_port))
            server_socket.listen(10)
            server_socket.settimeout(1)
            print(f"Seeder listening for incoming connections on port {self.listen_port}")

            while not self.exit_event.is_set():
                try:
                    client_socket, client_address = server_socket.accept()
                    print(f"Connected to {client_address}")
                    self.client_sockets.append(client_socket)
                    # Initialize statistics for the new leecher
                    with self.statistics_lock:
                        self.peer_statistics[client_address] = {'sent': 0, 'received': 0}
                    threading.Thread(target=self.handle_leecher_connection, args=(client_socket, client_address)).start()
                except socket.timeout:
                    continue  # Timeout, check if exit_event is set

    def start(self):
        self.register_with_tracker()
        # Start a thread to listen for quit or show command from user
        threading.Thread(target=self.listen_for_commands).start()
        # Start listening for leechers
        self.start_listening()

    def listen_for_commands(self):
        # Listen for 'quit' or 'show' commands in the terminal
        while not self.exit_event.is_set():
            command = input()
            if command.strip().lower() == "quit":
                print("Quitting the swarm...")
                self.exit_event.set()
                self.deregister_from_tracker()
                self.close_all_connections()
                break
            elif command.strip().lower() == "show":
                self.display_statistics()

    def handle_leecher_connection(self, leecher_socket, client_address):
        try:          
            # Continuously listen for requests for pieces
            while not self.exit_event.is_set():
                header = leecher_socket.recv(5)
                if len(header) < 5:
                    break
                message_length, message_id = struct.unpack("!IB", header)
                data = self._recv_exact(leecher_socket, message_length - 1)

                if message_id == BITFIELD:  # Bitfield message
                    self.receive_bitfield(leecher_socket, data)
                    self.send_bitfield(leecher_socket)
                elif message_id == BITFIELD_NO_LOOP:
                    self.receive_bitfield(leecher_socket, data)
                elif message_id == REQUEST:  # Request message
                    piece_index, = struct.unpack("!I", data)
                    self.send_piece(leecher_socket, piece_index, client_address)
                elif message_id == PIECE:  # Piece message
                    piece_index = struct.unpack("!I", data[:4])[0]
                    piece_data = data[4:]
                    self.log(f"DOWNLOADED {piece_index} FROM {client_address}")
                elif message_id == HAVE:
                    piece_index, = struct.unpack("!I", data)
                    self.log(f"{client_address} has {piece_index}")

        except (ConnectionResetError, BrokenPipeError):
            print(f"Connection to {client_address} lost.")
        except OSError as e:
            print(f"Connection to {client_address} CLOSED")
        finally:
            print(f"Closing connection to {client_address}")
            leecher_socket.close()

    def _recv_exact(self, sock, n):
        data = bytearray()
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                raise ConnectionError("Connection closed unexpectedly while receiving data.")
            data.extend(packet)
        return data
    
    def receive_bitfield(self, peer, bitfield):
        self.log(f"RECEIVED BD {bitfield} FROM {peer}")

    def send_bitfield(self, leecher_socket):
        message = struct.pack("!IB", 1 + len(self.bitfield), 5) + self.bitfield
        self._send_message(leecher_socket, message)
        self.log(f"SEND BD {message} TO {leecher_socket.getpeername()}")

    def send_piece(self, leecher_socket, piece_index, client_address):
        piece_data = self.piece_map.get(piece_index)
        if piece_data:
            piece_message = struct.pack("!IBI", 5 + len(piece_data), 7, piece_index) + piece_data
            self._send_message(leecher_socket, piece_message)
            self.log(f"SENT PIECE {piece_index} TO {client_address}.")
            # Update statistics
            with self.statistics_lock:
                self.peer_statistics[client_address]['sent'] += 1

    def _send_message(self, sock, message):
        try:
            sock.sendall(message)
        except (BrokenPipeError, ConnectionResetError):
            print("Failed to send message, connection may be closed.")

    def close_all_connections(self):
        # Close all active leecher connections
        for client_socket in self.client_sockets:
            try:
                client_socket.close()
            except OSError:
                pass
        self.client_sockets.clear()
        print("All connections closed.")

    def display_statistics(self):
        # Display the statistics for each connected peer
        print("\n--- Seeder Statistics ---")
        with self.statistics_lock:
            for peer, stats in self.peer_statistics.items():
                print(f"Peer {peer}: Sent: {stats['sent']}, Received: {stats['received']}")
        print("-------------------------")

# Example usage
import argparse

# Add this block to handle command-line arguments
parser = argparse.ArgumentParser(description="Run a torrent seeder.")
parser.add_argument("--piece_length", type=int, default=2048, help="Length of each piece in bytes.")
parser.add_argument("--port", type=int, default=6882, help="Port number for the seeder to listen on (default: 6882).")
parser.add_argument("--verbose", action="store_true", default = False, help="Enable detailed logging.")
args = parser.parse_args()

seeder = Seeder(folder_name="store", 
                piece_length=args.piece_length, 
                torrent_file_dest="file.torrent", 
                listen_port=args.port,
                tracker_url='http://192.168.1.9:8000',
                print_enabled=False)
seeder.start()