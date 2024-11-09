# torrent_file_process_leecher.py
import bencodepy
import requests
import bencode

class TorrentMetadata:
    def __init__(self, torrent_file_path):
        data = self.decode_bencode(torrent_file_path)
        # print(data)
        self.files = data['info']['files']
        self.piece_length = data['info']['piece length']
        #self.pieces = data['info']['pieces']
        #self.piece_hashes = [self.pieces[i:i+20] for i in range(0, len(self.pieces), 20)]
        self.piece_hashes = data['info']['pieces']
        self.piece_count = len(self.piece_hashes)
        self.md5sums = [file['md5sum'] for file in self.files]
        self.folder_name = data['info']['name']
        self.tracker_url = data['announce']

    @staticmethod
    def decode_bencode(file_path):
        with open(file_path, 'rb') as file:
            bencoded_data = file.read()
        return bencode.decode(bencoded_data)

def load_torrent_metadata(torrent_file_path):
    return TorrentMetadata(torrent_file_path)

def get_tracker_ip_port(torrent_metadata):
    # Send GET request to retrieve tracker information from 'tracker.txt'
    tracker_url = f"{torrent_metadata.tracker_url}/tracker.txt"
    try:
        response = requests.get(tracker_url)
        response.raise_for_status()
        tracker_ip, tracker_port = response.text.strip().split()
        return tracker_ip, int(tracker_port)
    except requests.RequestException as e:
        print(f"Failed to retrieve tracker information: {e}")
        return None, None
