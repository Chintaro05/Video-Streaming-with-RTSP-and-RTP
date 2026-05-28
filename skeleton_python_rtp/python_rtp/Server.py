import select
import sys, socket

from ServerWorker import ServerWorker

class Server:	
	
	def main(self):
		try:
			SERVER_PORT = int(sys.argv[1])
		except:
			print("[Usage: Server.py Server_port]\n")
		rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		rtspSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		rtspSocket.bind(('', SERVER_PORT))
		rtspSocket.listen(5)        
		rtspSocket.setblocking(False)

		workers = {}
		while True:
			readSockets = [rtspSocket] + list(workers.keys())
			readable, _, _ = select.select(readSockets, [], [], 0.01)

			for readySocket in readable:
				if readySocket is rtspSocket:
					clientSocket, clientAddress = rtspSocket.accept()
					clientSocket.setblocking(False)
					clientInfo = {'rtspSocket': (clientSocket, clientAddress)}
					workers[clientSocket] = ServerWorker(clientInfo)
				else:
					worker = workers.get(readySocket)
					if worker is None or not worker.recvRtspRequest():
						workers.pop(readySocket, None)
						try:
							readySocket.close()
						except OSError:
							pass

			for worker in list(workers.values()):
				worker.send_due_frames()

if __name__ == "__main__":
	(Server()).main()


