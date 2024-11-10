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
    def __init__(self, folder_name, piece_length, torrent_file_dest, listen_port=6881, tracker_ip="127.0.0.1", tracker_port=5008):
        self.folder_name = folder_name
        self.piece_length = piece_length
        self.torrent_file_dest = torrent_file_dest
        self.listen_port = listen_port
        self.listen_ip = socket.gethostbyname(socket.gethostname())
        self.tracker_ip = tracker_ip
        self.tracker_port = tracker_port
        self.piece_map = {}  # Mapping from piece index to piece data
        self.create_torrent_file()
        
        self.exit_event = threading.Event()  # Used to signal threads to exit
        self.client_sockets = []  # Track active connections to leechers

    def create_torrent_file(self):
        # Create the torrent file and initialize the piece mapping
        torrent_file_process.create_torrent_file(self.folder_name, self.piece_length, self.torrent_file_dest)
        self.piece_map = torrent_file_process.get_piece_map(self.folder_name, self.piece_length)
        self.bitfield = bytearray([1] * len(self.piece_map))  # All pieces are available

    def register_with_tracker(self):
        # Connect to the tracker and send listening port information
        try:
            self.tracker_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tracker_socket.connect((self.tracker_ip, self.tracker_port))
            self.tracker_socket.send(str(self.listen_port).encode())
            print(f"Registered with tracker at {self.tracker_ip}:{self.tracker_port} on port {self.listen_port}")
        except ConnectionError:
            print("Failed to connect to the tracker.")
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
                    threading.Thread(target=self.handle_leecher_connection, args=(client_socket, client_address)).start()
                except socket.timeout:
                    continue  # Timeout, check if exit_event is set

    def start(self):
        self.register_with_tracker()
        # Start a thread to listen for quit command from user
        threading.Thread(target=self.listen_for_quit).start()
        # Start listening for leechers
        self.start_listening()

    def listen_for_quit(self):
        # Listen for 'quit' command in terminal to exit the swarm
        while not self.exit_event.is_set():
            command = input()
            if command.strip().lower() == "quit":
                print("Quitting the swarm...")
                self.exit_event.set()
                self.deregister_from_tracker()
                self.close_all_connections()
                break

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
                    self.send_piece(leecher_socket, piece_index)
                elif message_id == PIECE:  # Piece message
                    piece_index = struct.unpack("!I", data[:4])[0]
                    piece_data = data[4:]
                    print(f"DOWNLOADED {piece_index} FROM {leecher_socket}")
                elif message_id == HAVE:
                    piece_index, = struct.unpack("!I", data)
                    print(f"{client_address} has {piece_index}")

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
        print(f"RECEIVED BD {bitfield} FROM {peer}")

    def send_bitfield(self, leecher_socket):
        message = struct.pack("!IB", 1 + len(self.bitfield), 5) + self.bitfield
        self._send_message(leecher_socket, message)
        print(f"SEND BD {message} TO {leecher_socket.getpeername()}")

    def send_piece(self, leecher_socket, piece_index):
        piece_data = self.piece_map.get(piece_index)
        if piece_data:
            piece_message = struct.pack("!IBI", 5 + len(piece_data), 7, piece_index) + piece_data
            self._send_message(leecher_socket, piece_message)
            print(f"SEND {piece_index} to {leecher_socket.getpeername()}.")

    def _send_message(self, sock, message):
        try:
            sock.sendall(message)
        except (BrokenPipeError, ConnectionResetError):
            print("Failed to send message, connection may be closed.")

    def close_all_connections(self):
        # Close all active leecher connections
        for client_socket in self.client_sockets:
            # print(f"CONNECTION WITH {client_socket.getpeername()} CLOSED")
            try:
                client_socket.close()
            except OSError:
                pass
        self.client_sockets.clear()
        print("All connections closed.")

# Example usage
seeder = Seeder(folder_name="store", piece_length=1024, torrent_file_dest="file.torrent")
seeder.start()
