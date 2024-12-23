import os
import hashlib
import math
import bencodepy

def create_torrent_file(folder_name, piece_length, torrent_file_dest, tracker_url="http://localhost:8000"):
    files_metadata = []
    all_piece_hashes = []

    # Collect file metadata and piece hashes
    for file_name in sorted(os.listdir(folder_name)):
        file_path = os.path.join(folder_name, file_name)
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            piece_hashes = calculate_piece_hashes(file_path, piece_length)
            
            all_piece_hashes.extend(piece_hashes)

            file_metadata = {
                "length": file_size,
                "md5sum": hashlib.md5(open(file_path, 'rb').read()).hexdigest(),
                "filename": file_name
            }
            files_metadata.append(file_metadata)

    # Concatenate all piece hashes for bencoding
    # pieces = b''.join(all_piece_hashes)
    pieces = all_piece_hashes
    # print(pieces)
    # Create torrent metadata structure
    torrent_info = {
        "name": os.path.basename(folder_name),
        "files": files_metadata,
        "piece length": piece_length,
        "pieces": pieces
    }

    torrent_metadata = {
        "announce": tracker_url,
        "info": torrent_info
    }

    # Encode and save .torrent file
    # print(torrent_metadata)
    encoded_data = bencodepy.encode(torrent_metadata)
    with open(torrent_file_dest, 'wb') as torrent_file:
        torrent_file.write(encoded_data)
    print(f"Torrent file created at: {torrent_file_dest}")

def calculate_piece_hashes(file_path, piece_length):
    piece_hashes = []
    with open(file_path, 'rb') as f:
        while True:
            piece_data = f.read(piece_length)
            if not piece_data:
                break
            piece_hash = hashlib.sha1(piece_data).hexdigest()
            piece_hashes.append(piece_hash)
    return piece_hashes

def get_piece_map(folder_name, piece_length):
    piece_map = {}
    piece_index = 0

    # Process each file in the folder
    for file_name in sorted(os.listdir(folder_name)):
        file_path = os.path.join(folder_name, file_name)
        if os.path.isfile(file_path):
            with open(file_path, 'rb') as f:
                while True:
                    piece_data = f.read(piece_length)
                    if not piece_data:
                        break
                    piece_map[piece_index] = piece_data
                    piece_index += 1

    print(f"Piece map created with {len(piece_map)} pieces.")
    return piece_map

import requests
def get_tracker_ip_port(tracker_url):
    # Send GET request to retrieve tracker information from 'tracker.txt'
    tracker_url = f"{tracker_url}/tracker.txt"
    try:
        response = requests.get(tracker_url)
        response.raise_for_status()
        tracker_ip, tracker_port = response.text.strip().split()
        return tracker_ip, int(tracker_port)
    except requests.RequestException as e:
        print(f"Failed to retrieve tracker information: {e}")
        return None, None

