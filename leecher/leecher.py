import socket
import threading
import torrent_file_process
import pickle
import os
import struct
import hashlib
import random
import time

BITFIELD = 4
BITFIELD_NO_LOOP = 5
REQUEST = 6
PIECE = 7
HAVE = 8

class Leecher:
    def __init__(self, torrent_file_path, download_folder, port, random_bool, print_enabled):
        self.torrent_file_path = torrent_file_path
        self.download_folder = download_folder
        self.peer_list = []
        self.random_bool = random_bool
        # Dictionaries for peer management and piece tracking
        self.socket_dic = {}
        self.bitfield_dic = {}
        self.piece_has = {}
        self.dup = 0

        self.my_pieces = set()
        if (port is None):
            self.listening_port = random.randint(6000, 9000)
        else:
            self.listening_port = port
        self.listening_ip = socket.gethostbyname(socket.gethostname())
        self.piece_length = None
        self.piece_count = 0
        self.piece_hashes = []
        self.downloaded_pieces = {}
        self.peer_statistics = {}

        # Fine-grained locks for each shared structure
        self.peer_list_lock = threading.Lock()
        self.socket_dic_lock = threading.Lock()
        self.piece_has_lock = threading.Lock()
        self.downloaded_pieces_lock = threading.Lock()
        self.socket_locks = {}
        self.exit_event = threading.Event()  
        self.my_pieces_lock = threading.Lock()
        self.statistics_lock = threading.Lock()

        self.print_enabled = print_enabled  # Enable/disable printing

    def log(self, message):
        if self.print_enabled:
            print(message)

    def parse_torrent_file(self):
        # Load metadata from the torrent file
        self.metadata = torrent_file_process.load_torrent_metadata(self.torrent_file_path)
        self.piece_length = self.metadata.piece_length
        self.piece_hashes = self.metadata.piece_hashes
        self.piece_count = self.metadata.piece_count
        self.folder_name = self.metadata.folder_name
        print(f"Parsed torrent file: {self.piece_count} pieces of size {self.piece_length}")

    def register_with_tracker(self):
        tracker_ip, tracker_port = torrent_file_process.get_tracker_ip_port(self.metadata)
        if tracker_ip and tracker_port:
            print(f"Retrieved tracker IP: {tracker_ip}, Port: {tracker_port}")
            self.tracker_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tracker_socket.connect((tracker_ip, tracker_port))
            self.tracker_socket.send(str(self.listening_port).encode())
            print(f"LISTENING AT: {self.listening_ip} : {self.listening_port}")
            self.init_with_peers()
            threading.Thread(target=self.receive_tracker_updates).start()
        else:
            print("Failed to retrieve tracker information.")
    
    def init_with_peers(self):
        with self.peer_list_lock:
            self.peer_list = pickle.loads(self.tracker_socket.recv(4096))
            self.peer_list.remove((self.listening_ip, self.listening_port))
            
            print(f"ORIGINAL PEER LIST {self.peer_list}")
            for peer in self.peer_list:
                if peer != (self.listening_ip, self.listening_port):
                    self.connect_to_peer(peer)

    def receive_tracker_updates(self):
        while not self.exit_event.is_set():
            try:
                updated_peer_list = pickle.loads(self.tracker_socket.recv(4096))
                updated_peer_list.remove((self.listening_ip, self.listening_port))
                self.update_peer_list(updated_peer_list)
            except ConnectionError:
                break

    def update_peer_list(self, updated_list):
        print(f"UPDATED LIST {updated_list}")
        with self.peer_list_lock:
            # Remove peers that are no longer in the list
            to_remove = [peer for peer in self.peer_list if peer not in updated_list]
            for peer in to_remove:
                self.remove_peer_socket(peer)
            self.peer_list = updated_list
            
    def remove_peer_socket(self, peer):
        # Close the socket and remove the peer from the list and dictionary
        with self.socket_dic_lock:
            if peer in self.socket_dic:
                self.socket_dic[peer].close()
                del self.socket_dic[peer]

    def connect_to_peer(self, peer):
        try:
            peer_socket = socket.create_connection(peer)
            with self.socket_dic_lock:
                self.socket_dic[peer] = peer_socket
                # self.socket_locks[peer] = threading.Lock()
            self.send_bitfield(peer)
            # Initialize statistics for the peer
            with self.statistics_lock:
                self.peer_statistics[peer] = {'sent': 0, 'received': 0}
            threading.Thread(target=self.receive_messages, args=(peer,)).start()
        except (ConnectionRefusedError, OSError) as e:
            print(f"Could not connect to peer {peer} {e}")

    def listen_for_incoming_connections(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.bind(('', self.listening_port))
            server_socket.listen(10)
            server_socket.settimeout(1)
            print(f"Leecher listening at {self.listening_ip} {self.listening_port}")
            
            while not self.exit_event.is_set():
                try:
                    client_socket, client_address = server_socket.accept()
                    print(f"Accepted connection from {client_address}")
                    with self.socket_dic_lock:
                        self.socket_dic[client_address] = client_socket
                        self.socket_locks[client_address] = threading.Lock()
                    with self.statistics_lock:
                        self.peer_statistics[client_address] = {'sent': 0, 'received': 0}
                    threading.Thread(target=self.receive_messages, args=(client_address,)).start()
                except socket.timeout:
                    continue

    def send_bitfield(self, peer, loop = True):
        bitfield_payload = bytearray(self.piece_count)
        with self.my_pieces_lock:
            for index in self.my_pieces:
                bitfield_payload[index] = 1
        self.log(f"SEND BD to {peer}")
        if loop:
            message = struct.pack("!IB", 1 + len(bitfield_payload), BITFIELD) + bitfield_payload
        else:
            message = struct.pack("!IB", 1 + len(bitfield_payload), BITFIELD_NO_LOOP) + bitfield_payload
        self._send_message(peer, message)

    def receive_messages(self, peer):
        peer_socket = self.socket_dic[peer]
        print(f"LISTENING TO {peer}")
        while not self.exit_event.is_set():
            try:
                header = peer_socket.recv(5)
                if len(header) < 5:
                    break
                message_length, message_id = struct.unpack("!IB", header)
                data = self._recv_exact(peer_socket, message_length - 1)

                if message_id == BITFIELD:  # Bitfield message
                    self.receive_bitfield(peer, data)
                    self.send_bitfield(peer, loop=False)
                elif message_id == BITFIELD_NO_LOOP:
                    self.receive_bitfield(peer, data)
                elif message_id == REQUEST:  # Request message
                    piece_index, = struct.unpack("!I", data)
                    self.send_piece(peer, piece_index)
                elif message_id == PIECE:  # Piece message
                    piece_index = struct.unpack("!I", data[:4])[0]
                    piece_data = data[4:]
                    self.process_piece(piece_index, piece_data, peer)
                elif message_id == HAVE:
                    piece_index, = struct.unpack("!I", data)
                    self.process_have_message(peer, piece_index)
            except (BrokenPipeError, ConnectionResetError):
                print(f"Connection with {peer} lost")
                break
            except OSError as e:
                print(f"Connection with {peer} CLOSED")

    def process_have_message(self, peer, piece_index):
        # Update bitfield_dic with the new piece for the peer
        self.log(f"{peer} has {piece_index}")
        with self.piece_has_lock:
            if peer in self.bitfield_dic:
                # Update the peer's bitfield to indicate they have this piece
                bitfield = bytearray(self.bitfield_dic[peer])
                bitfield[piece_index] = 1
                self.bitfield_dic[peer] = bitfield
            else:
                # Initialize the bitfield if it doesn't exist
                self.bitfield_dic[peer] = bytearray(self.piece_count)
                self.bitfield_dic[peer][piece_index] = 1

        # Update the piece_has dictionary to add the peer to the list of peers with this piece
        with self.piece_has_lock:
            if piece_index not in self.piece_has:
                self.piece_has[piece_index] = []
            if peer not in self.piece_has[piece_index]:
                self.piece_has[piece_index].append(peer)

    def send_piece(self, peer, piece_index):
        
        with self.downloaded_pieces_lock:
            # Check if the requested piece is available
            if piece_index in self.downloaded_pieces:
                with self.piece_has_lock:
                    peer_bitfield = self.bitfield_dic.get(peer, bytearray(self.piece_count))
                    if peer_bitfield[piece_index] == 1:
                        print(f"{peer} HAD {piece_index} NO SEND")
                        return
                piece_data = self.downloaded_pieces[piece_index]
                # Construct the piece message: length, ID=7, piece_index, and piece_data
                message = struct.pack("!IBI", 5 + len(piece_data), PIECE, piece_index) + piece_data
                # Send the piece message
                self._send_message(peer, message)
                self.log(f"SENT PIECE {piece_index} TO {peer}")
                with self.statistics_lock:
                    self.peer_statistics[peer]['sent'] += 1
            else:
                # Log that the requested piece is not available
                print(f"Requested piece {piece_index} not available for {peer}")

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
        with self.piece_has_lock:
            self.bitfield_dic[peer] = bitfield
            for piece_index, has_piece in enumerate(bitfield):
                if has_piece:
                    self.update_peer_piece_info(peer, piece_index)

    def update_peer_piece_info(self, peer, piece_index):
        if piece_index not in self.piece_has:
            self.piece_has[piece_index] = []
        if peer not in self.piece_has[piece_index]:
            self.piece_has[piece_index].append(peer)

    def _send_message(self, peer, message):
        with self.socket_dic_lock:
            peer_socket = self.socket_dic.get(peer)
        if peer_socket:
            try:
                peer_socket.sendall(message)
            except (BrokenPipeError, ConnectionResetError):
                print(f"Failed to send message to {peer}")

    def request_piece(self, piece_index):
        with self.piece_has_lock:
            peers_with_piece = self.piece_has.get(piece_index, [])
        self.log(f"LIST: {peers_with_piece} has {piece_index}")
        if peers_with_piece:
            peer = random.choice(peers_with_piece)
            message = struct.pack("!IBI", 5, 6, piece_index)
            self._send_message(peer, message)
            self.log(f"SENT REQUEST {piece_index} to {peer}")

    def process_piece(self, piece_index, piece_data, peer):
        if (piece_index in self.my_pieces):
            self.dup += 1
            return
        with self.downloaded_pieces_lock:
            self.downloaded_pieces[piece_index] = piece_data
            with self.statistics_lock:
                self.peer_statistics[peer]['received'] += 1
            self.log(f"DOWNLOADED {piece_index} FROM {peer}")
        # piece_path = os.path.join(self.download_folder, f"piece_{piece_index}")
        # with open(piece_path, "wb") as piece_file:
        #     piece_file.write(piece_data)
        if self.verify_piece(piece_index, piece_data):
            with self.my_pieces_lock:
                self.my_pieces.add(piece_index)
            self.broadcast_have(piece_index)
        else:
            self.log(f"{piece_index} NOT VALID")
    def verify_piece(self, piece_index, piece_data):
        expected_hash = self.piece_hashes[piece_index]
        actual_hash = hashlib.sha1(piece_data).hexdigest()
        return actual_hash == expected_hash

    def broadcast_have(self, piece_index):
        with self.peer_list_lock:
            for peer in self.peer_list:
                message = struct.pack("!IBI", 5, HAVE, piece_index)
                self._send_message(peer, message)
                self.log(f"SENT HAVE {piece_index} to {peer}")

    def download_pieces(self):
        piece_indexes = list(range(self.piece_count))
        i = 0
        print("START SENDING")
        while i < 30:
            i += 1
            
            with self.my_pieces_lock:
                not_downloaded_set = set(piece_indexes) - self.my_pieces

            print(len(not_downloaded_set), i)
            if len(not_downloaded_set) == 0:
                break

            not_downloaded_list = list(not_downloaded_set)
            if self.random_bool:
                random.shuffle(not_downloaded_list)

            for piece_index in not_downloaded_list:
                # Double-check under lock before requesting a piece
                check = True
                with self.my_pieces_lock:
                    check = piece_index not in self.my_pieces
                if check:
                    self.request_piece(piece_index)
                else:
                    self.log("ERROR DOWNLOAD PIECES")

            print(f"NOT DOWNLOADED {len(not_downloaded_set)}")
            time.sleep(float(len(not_downloaded_set)) / 10000)


        print("SENT ALL REQUEST")
        while len(self.downloaded_pieces) < self.piece_count:
            not_downloaed_set = set(range(self.piece_count)) - self.my_pieces
            time.sleep(0.5)
            print(f"DOWNLOADING {len(self.downloaded_pieces)} / {self.piece_count}", end = '\r')
            print(f"NOT DOWNLOADED {not_downloaed_set}")
        print(f"DOWNLOADING {len(self.downloaded_pieces)} / {self.piece_count}", end = '\r')
        print("All pieces downloaded.")

    def display_statistics(self):
        print("\n--- Statistics ---")
        print(self.dup)
        with self.peer_list_lock:
            for peer, stats in self.peer_statistics.items():
                print(f"Peer {peer}: Sent: {stats['sent']}, Received: {stats['received']}")
        print("------------------")

    def simu_download_pieces(self):
        # Create a list of all pieces and shuffle it for random download order
        piece_indices = list(range(self.piece_count))
        if (self.random_bool):
            random.shuffle(piece_indices)

        # Limit the number of concurrent download threads to avoid overwhelming the system
        max_concurrent_downloads = 5  # You can adjust this based on system capacity
        active_threads = []

        for piece_index in piece_indices:
            # Skip if the piece is already downloaded
            if piece_index in self.my_pieces:
                continue

            # Start a new thread to download this piece
            thread = threading.Thread(target=self.download_piece_thread, args=(piece_index,))
            thread.start()
            active_threads.append(thread)

            # Maintain a maximum number of concurrent threads
            if len(active_threads) >= max_concurrent_downloads:
                # Wait for any thread to finish before starting a new one
                for t in active_threads:
                    t.join(0.1)  # Short wait to allow threads to finish
                    if not t.is_alive():
                        active_threads.remove(t)

        # Wait for any remaining threads to complete
        for t in active_threads:
            t.join()

        print("SENT ALL REQUEST")
        while len(self.downloaded_pieces) < self.piece_count:
            not_downloaed_set = set(range(self.piece_count)) - self.my_pieces
            time.sleep(0.5)
            print(f"DOWNLOADING {len(self.downloaded_pieces)} / {self.piece_count}", end = '\r')
            print(f"NOT DOWNLOADED {not_downloaed_set}")
        print(f"DOWNLOADING {len(self.downloaded_pieces)} / {self.piece_count}", end = '\r')
        print("All pieces downloaded.")

    def download_piece_thread(self, piece_index):
        with self.piece_has_lock:
            # Get available peers with the required piece
            peers_with_piece = self.piece_has.get(piece_index, [])
        if not peers_with_piece:
            self.log(f"No peers with piece {piece_index} available.")
            return

        # Choose a random peer and request the piece
        peer = random.choice(peers_with_piece)
        message = struct.pack("!IBI", 5, REQUEST, piece_index)
        self.log(f"REQUEST PIECE {piece_index} FROM {peer}")
        self._send_message(peer, message)

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
    
    def quit_swarm(self):
        print("Leaving the swarm...")
        self.exit_event.set()
        
        # Notify tracker to remove this peer
        self.tracker_socket.send(b"quit")
        self.tracker_socket.close()
        
        # Close all peer connections
        with self.socket_dic_lock:
            for peer, sock in self.socket_dic.items():
                sock.close()

        print("Exited the swarm.")

    def input_handle(self):
        while not self.exit_event.is_set():
            user_input = input()
            if user_input.lower() == "quit":
                self.quit_swarm()
                break
            elif user_input.lower() == "show":
                self.display_statistics()

    
    def start(self, mode = 0):
        start = time.time()
        self.parse_torrent_file()
        self.register_with_tracker()
        threading.Thread(target=self.listen_for_incoming_connections).start()
        threading.Thread(target=self.input_handle).start()
        time.sleep(1)
        if (mode == 1):
            self.simu_download_pieces()
        elif (mode == 0):
            self.download_pieces()

        self.assemble_files()
        #self.display_statistics()
        return (time.time() - start)


import argparse
import sys

parser = argparse.ArgumentParser(description="Leecher in a P2P network")
parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
parser.add_argument("--mode", type=int, default=0, help="Mode of operation: 0 for sequential, 1 for parallel")
parser.add_argument("--random", action="store_true", help="Enable random piece downloading")
parser.add_argument("--port", type=int, default = None, help="Port number for listening connections")

args = parser.parse_args(sys.argv[1:])
leecher = Leecher(
    torrent_file_path="file.torrent",
    download_folder="downloads",
    port=args.port,
    random_bool=args.random,
    print_enabled=args.verbose
)
print(leecher.start(mode=args.mode))
