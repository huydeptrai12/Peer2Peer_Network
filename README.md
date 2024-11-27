# P2P_Network
This is a BitTorrent-like system. 
If this is helpful somehow, please give me a star
This is a really big file of code, this takes me 2 weeks to design and code all these.
Although it is not something new or crazy, I learnt a lot from this.
# HOW TO RUN THIS SYSTEM
1. Go to tracker folder, run the server.py
2. Then, direct to seeder folder, in the store folder, add any file you want to share to other peers. Then, run seeder.py. After running seeder.py, it will create a torrent file for the system.
3. Copy the torrent file into the leecher folder, then run leecher.py. You should run the leecher.py by writing prompt in the terminal. Eg: python leecher.py mode, the mode here is either 0 or 1. 0 is for single piece downloading mode and 1 is for multiple piece downloading mode.
4. If you want to run many leecher, make sure that you copy the torrnet file into that leecher folder.
5. That should be it, the leecher will download all the file in the store folder in the seeder.
# DOCUMENT
Well, I haven't done this but this will be available soon ! 
