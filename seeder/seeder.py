# seeder.py
import socket
import threading
import struct
import torrent_file_process

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
            print(f"LISTENING AT: {self.listen_ip} : {self.listen_port}")
        except ConnectionError:
            print("Failed to connect to the tracker.")
            self.tracker_socket = None

    def start_listening(self):
        # Register with the tracker so leechers can discover this seeder
        self.register_with_tracker()

        # Start a TCP server to listen for incoming connections from leechers
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.bind(('', self.listen_port))
            server_socket.listen(5)
            print(f"Seeder listening for incoming connections on port {self.listen_port}")

            while True:
                client_socket, client_address = server_socket.accept()
                print(f"Connected to leecher at {client_address}")
                threading.Thread(target=self.handle_leecher_connection, args=(client_socket, client_address)).start()

    def handle_leecher_connection(self, leecher_socket, client_address):
        try:
            leecher_bitfield = self.receive_bitfield(leecher_socket)
            print(f"BITFIELD FROM {client_address} {leecher_bitfield}")
            print("Received bitfield from leecher, responding with seeder's bitfield.")
            self.send_bitfield(leecher_socket)

            # Continuously listen for requests for pieces
            while True:
                data = leecher_socket.recv(5)
                if len(data) < 5:
                    break

                message_length, message_id = struct.unpack("!IB", data[:5])

                if message_id == 6:
                    piece_index, = struct.unpack("!I", leecher_socket.recv(4))
                    self.send_piece(leecher_socket, piece_index)

        except (ConnectionResetError, BrokenPipeError):
            print("Connection to leecher lost.")
        finally:
            leecher_socket.close()

    def receive_bitfield(self, leecher_socket):
        header = leecher_socket.recv(5)
        if len(header) < 5:
            raise ConnectionError("Failed to receive bitfield header from leecher")

        message_length, message_id = struct.unpack("!IB", header)
        if message_id != 5:
            raise ValueError("Expected bitfield message from leecher")

        bitfield = leecher_socket.recv(message_length - 1)
        return bitfield

    def send_bitfield(self, leecher_socket):
        message = struct.pack("!IB", 1 + len(self.bitfield), 5) + self.bitfield
        print(message)
        self._send_message(leecher_socket, message)
        print("Sent bitfield to leecher indicating all pieces are available.")

    def send_piece(self, leecher_socket, piece_index):
        piece_data = self.piece_map.get(piece_index)
        if piece_data:
            piece_message = struct.pack("!IBI", 5 + len(piece_data), 7, piece_index) + piece_data
            self._send_message(leecher_socket, piece_message)
            print(f"Sent piece {piece_index} to leecher.")

    def _send_message(self, sock, message):
        try:
            sock.sendall(message)
        except (BrokenPipeError, ConnectionResetError):
            print("Failed to send message, connection may be closed.")

# Example usage
seeder = Seeder(folder_name="store", piece_length=1024, torrent_file_dest="file.torrent")
seeder.start_listening()
