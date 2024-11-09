# leecher.py
import socket
import threading
import torrent_file_process
import pickle
import os
import struct
import hashlib
import random
import time

class Leecher:
    def __init__(self, torrent_file_path, download_folder):
        self.torrent_file_path = torrent_file_path
        self.download_folder = download_folder
        self.peer_list = []  # List of (peer_ip, peer_port)
        
        # Dictionaries for peer management and piece tracking
        self.socket_dic = {}       # { (peer_ip, peer_port): socket_connection }
        self.bitfield_dic = {}     # { (peer_ip, peer_port): bitfield }
        self.piece_has = {}        # { piece_index: [list_of_peers] }

        self.my_pieces = set()     # Track pieces this leecher has
        self.listening_port = 6969  # Will be set on registration
        self.listening_ip = socket.gethostbyname(socket.gethostname())
        self.piece_length = None   # Piece length to be extracted from the torrent file
        self.piece_count = 0       # Total number of pieces, also from the torrent file
        self.piece_hashes = []     # Piece hashes from torrent file
        self.downloaded_pieces = {}
        # Locks for synchronization
        self.lock = threading.Lock()

    def parse_torrent_file(self):
        # Load metadata from the torrent file
        self.metadata = torrent_file_process.load_torrent_metadata(self.torrent_file_path)
        self.piece_length = self.metadata.piece_length
        self.piece_hashes = self.metadata.piece_hashes
        self.piece_count = self.metadata.piece_count
        self.folder_name = self.metadata.folder_name
        print(f"Parsed torrent file: {self.piece_count} pieces of size {self.piece_length}")

    def register_with_tracker(self):
        # Retrieve tracker IP and port using the function from torrent_file_process
        tracker_ip, tracker_port = torrent_file_process.get_tracker_ip_port(self.metadata)
        if tracker_ip and tracker_port:
            print(f"Retrieved tracker IP: {tracker_ip}, Port: {tracker_port}")
            self.tracker_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tracker_socket.connect((tracker_ip, tracker_port))
            # self.listening_port = self.tracker_socket.getsockname()[1]
            self.tracker_socket.send(str(self.listening_port).encode())
            print(f"LISTENING AT: {self.listening_ip} : {self.listening_port}")
            # Start a thread to continuously receive updated peer lists from the tracker
            threading.Thread(target=self.receive_tracker_updates).start()
        else:
            print("Failed to retrieve tracker information.")

    def receive_tracker_updates(self):
        # Continuously receive updated peer lists from the tracker
        while True:
            updated_peer_list = pickle.loads(self.tracker_socket.recv(4096))
            updated_peer_list.remove((self.listening_ip, self.listening_port))
            print(f"UPDATED PEER_LIST: {updated_peer_list}")
            self.update_peer_list(updated_peer_list)

    def update_peer_list(self, updated_list):
        # Add only new peers to the peer_list and start connections
        with self.lock:
            new_peers = [peer for peer in updated_list if peer not in self.peer_list]
            self.peer_list.extend(new_peers)

        for peer in new_peers:  
            if (peer != (self.listening_ip, self.listening_port)):
                self.connect_to_peer(peer)

    def connect_to_peer(self, peer):
        # Connect to a new peer and add it to the socket dictionary
        try:
            peer_socket = socket.create_connection(peer)
            self.socket_dic[peer] = peer_socket
            # Send bitfield to indicate which pieces this leecher has
            self.send_bitfield(peer)
            # Start a listener thread for messages from this peer
            threading.Thread(target=self.receive_messages, args=(peer,)).start()
        except (ConnectionRefusedError, OSError):
            print(f"Could not connect to peer {peer}")

    def send_bitfield(self, peer):
        # Send the bitfield message to a specific peer
        bitfield_payload = bytearray(self.piece_count)
        for index in self.my_pieces:
            bitfield_payload[index] = 1
        # bitfield_payload = bytearray([0] * self.piece_count)
        message = struct.pack("!IB", 1 + len(bitfield_payload), 5) + bitfield_payload  # ID 5 for bitfield
        print(message)
        self._send_message(peer, message)

    def receive_messages(self, peer):
        # Continuously listen for messages from a connected peer
        peer_socket = self.socket_dic[peer]
        print(f"LISTENING TO {peer}")
        while True:
            try:
                # Read the message length prefix
                header = peer_socket.recv(5)
                if len(header) < 5:
                    break  # Incomplete message, connection may be closed
                message_length, message_id = struct.unpack("!IB", header)

                # Read the full message based on the length prefix
                data = self._recv_exact(peer_socket, message_length - 1)

                if message_id == 5:  # Bitfield message
                    self.receive_bitfield(peer, data)
                elif message_id == 6:  # Request message
                    piece_index, = struct.unpack("!I", data)
                    self.send_piece(peer, piece_index)
                elif message_id == 7:  # Piece message
                    piece_index = struct.unpack("!I", data[:4])[0]
                    piece_data = data[4:]
                    self.process_piece(piece_index, piece_data)
            except (BrokenPipeError, ConnectionResetError):
                print(f"Connection with {peer} lost")
                break

    def receive_bitfield(self, peer, bitfield):
        # Process received bitfield and update piece availability
        print(f"RECEIVED BIT FIELD MESSAGE FROM {peer}")
        with self.lock:
            self.bitfield_dic[peer] = bitfield
            for piece_index, has_piece in enumerate(bitfield):
                if has_piece:
                    self.update_peer_piece_info(peer, piece_index)

    def update_peer_piece_info(self, peer, piece_index):
        # Update the piece_has dictionary with information from the peer's bitfield
        with self.lock:
            if piece_index not in self.piece_has:
                self.piece_has[piece_index] = []
            if peer not in self.piece_has[piece_index]:
                self.piece_has[piece_index].append(peer)

    def _send_message(self, peer, message):
        # Helper to send a message to a peer using an established socket
        peer_socket = self.socket_dic.get(peer)
        if peer_socket:
            try:
                peer_socket.sendall(message)
            except (BrokenPipeError, ConnectionResetError):
                print(f"Failed to send message to {peer}")

    def _recv_exact(self, sock, n):
        # Helper to receive exactly `n` bytes from the socket
        data = bytearray()
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                raise ConnectionError("Connection closed unexpectedly")
            data.extend(packet)
        return data

    def request_piece(self, piece_index):
        # Request a specific piece from a randomly selected peer that has it
        print(f"REQUEST {piece_index} {self.lock.locked()}")
        with self.lock:
            print(f"REQUEST {piece_index} {self.lock.locked()}")
            peers_with_piece = self.piece_has.get(piece_index, [])
            if peers_with_piece:
                print(f"FOUND PIECE {piece_index} at {peers_with_piece}")
                peer = random.choice(peers_with_piece)  # Randomly select one peer
                message = struct.pack("!IBI", 5, 6, piece_index)  # ID 6 for request
                self._send_message(peer, message)
            print(f"REQUEST {piece_index} {self.lock.locked()}")

    def send_piece(self, peer, piece_index):
        # Send a piece message (ID 7) to a peer that requested it
        piece_path = os.path.join(self.download_folder, f"piece_{piece_index}")
        try:
            with open(piece_path, "rb") as piece_file:
                piece_data = piece_file.read()
            piece_message = struct.pack("!IBI", 5 + len(piece_data), 7, piece_index) + piece_data
            self._send_message(peer, piece_message)
            print(f"Sent piece {piece_index} to peer {peer}")
        except FileNotFoundError:
            print(f"Requested piece {piece_index} not found for peer {peer}")

    def process_piece(self, piece_index, piece_data):
        # Process and verify received piece
        piece_path = os.path.join(self.download_folder, f"piece_{piece_index}")
        self.downloaded_pieces[piece_index] = piece_data
        with open(piece_path, "wb") as piece_file:
            piece_file.write(piece_data)
        if self.verify_piece(piece_index, piece_data):
            self.my_pieces.add(piece_index)
            self.broadcast_have(piece_index)

    def verify_piece(self, piece_index, piece_data):
        # Verify the integrity of a piece using its hash
        expected_hash = self.piece_hashes[piece_index]
        actual_hash = hashlib.sha1(piece_data).hexdigest()
        return actual_hash == expected_hash

    def broadcast_have(self, piece_index):
        # Notify peers about a newly downloaded piece
        with self.lock:
            for peer in self.peer_list:
                message = struct.pack("!IBI", 5, 7, piece_index)  # ID 7 for have
                self._send_message(peer, message)

    def download_pieces(self):
        # Request missing pieces from peers
        for piece_index in range(self.piece_count):
            if piece_index not in self.my_pieces:
                self.request_piece(piece_index)

        while len(self.downloaded_pieces) < self.piece_count:
            print(f"Downloaded {len(self.downloaded_pieces)}/{self.piece_count} pieces.")
            time.sleep(1)  # Avoids busy-waiting, check periodically

        print("All pieces downloaded.")

    def assemble_files(self):
        output_folder = os.path.join(self.download_folder, self.metadata.folder_name)
        os.makedirs(output_folder, exist_ok=True)
        
        piece_index = 0
        for file_info in self.metadata.files:
            file_name = file_info['filename']
            file_length = file_info['length']
            file_path = os.path.join(output_folder, file_name)

            with open(file_path, 'wb') as file:
                bytes_written = 0
                while bytes_written < file_length:
                    piece_data = self.downloaded_pieces[piece_index]
                    bytes_to_write = min(len(piece_data), file_length - bytes_written)
                    file.write(piece_data[:bytes_to_write])
                    bytes_written += bytes_to_write
                    piece_index += 1
            print(f"Assembled file: {file_name}, size: {file_length}")
            
    def start(self):
        # Parse the torrent file, register with the tracker, and start downloading/uploading
        self.parse_torrent_file()
        self.register_with_tracker()
        time.sleep(5)
        self.download_pieces()
        self.assemble_files()

# Example usage 
leecher = Leecher(torrent_file_path="file.torrent", download_folder="downloads")
leecher.start()
