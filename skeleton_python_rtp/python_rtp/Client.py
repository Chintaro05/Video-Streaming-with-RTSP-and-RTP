from tkinter import *
import tkinter.messagebox as tkMessageBox
from tkinter import ttk
import tkinter.font as tkFont
from PIL import Image, ImageTk
from collections import deque
import os
import socket
import struct
import threading

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"
FRAGMENT_HEADER = struct.Struct("!2sHHH")
FRAGMENT_MAGIC = b"FR"
TCP_FRAME_HEADER = struct.Struct("!BBH")
PREBUFFER_FRAMES = 20
MAX_BUFFER_FRAMES = PREBUFFER_FRAMES
PLAYBACK_DELAY_MS = 50


class Client:
	INIT = 0
	READY = 1
	PLAYING = 2

	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3
	PREFETCH = 4

	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.qualityMode = StringVar(value="Auto")
		self.state = self.INIT
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.pendingRequests = {}
		self.teardownAcked = 0
		self.frameNbr = 0
		self.transport = self.selectedTransport()
		self.frameBuffer = deque()
		self.bufferLock = threading.Lock()
		self.fragments = {}
		self.playEvent = threading.Event()
		self.receiverRunning = True
		self.rtpListenerStarted = False
		self.rtspBuffer = b""
		self.currentImageFile = None
		self.resizeJob = None
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.updateControls()
		self.connectToServer()

	def createWidgets(self):
		"""Build GUI."""
		self.fontFamily = self.preferredFont()
		self.master.configure(bg="#eef2f6")
		self.master.minsize(900, 620)
		self.master.columnconfigure(0, weight=1)
		self.master.rowconfigure(1, weight=1)

		style = ttk.Style()
		try:
			style.theme_use("clam")
		except TclError:
			pass
		style.configure(".", font=(self.fontFamily, 10))
		style.configure("App.TFrame", background="#eef2f6")
		style.configure("Panel.TFrame", background="#ffffff", relief=FLAT)
		style.configure("Header.TLabel", background="#eef2f6", foreground="#17202a", font=(self.fontFamily, 18, "bold"))
		style.configure("Meta.TLabel", background="#eef2f6", foreground="#607080", font=(self.fontFamily, 10))
		style.configure("PanelTitle.TLabel", background="#ffffff", foreground="#273746", font=(self.fontFamily, 10, "bold"))
		style.configure("PanelText.TLabel", background="#ffffff", foreground="#566573", font=(self.fontFamily, 10))
		style.configure("Status.TLabel", background="#dbeafe", foreground="#1e3a5f", font=(self.fontFamily, 10))
		style.configure("Primary.TButton", background="#2563eb", foreground="#ffffff", borderwidth=0, padding=(16, 10), font=(self.fontFamily, 10, "bold"))
		style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#b7c7e9")], foreground=[("disabled", "#f4f7fb")])
		style.configure("Secondary.TButton", background="#e7edf5", foreground="#1f2d3d", borderwidth=0, padding=(16, 10), font=(self.fontFamily, 10, "bold"))
		style.map("Secondary.TButton", background=[("active", "#d7e1ee"), ("disabled", "#edf1f5")], foreground=[("disabled", "#9aa6b2")])
		style.configure("Danger.TButton", background="#fee2e2", foreground="#991b1b", borderwidth=0, padding=(16, 10), font=(self.fontFamily, 10, "bold"))
		style.map("Danger.TButton", background=[("active", "#fecaca"), ("disabled", "#f4eeee")], foreground=[("disabled", "#b0a2a2")])
		style.configure("Quality.TCombobox", padding=(8, 6), arrowsize=14)
		style.configure("Buffer.Horizontal.TProgressbar", background="#10b981", troughcolor="#e8eef5", bordercolor="#e8eef5", lightcolor="#10b981", darkcolor="#10b981")

		header = ttk.Frame(self.master, style="App.TFrame", padding=(24, 18, 24, 10))
		header.grid(row=0, column=0, sticky=W+E)
		header.columnconfigure(0, weight=1)

		title = ttk.Label(header, text="RTSP/RTP Video Streaming", style="Header.TLabel")
		title.grid(row=0, column=0, sticky=W)

		sourceText = self.serverAddr + ":" + str(self.serverPort) + "  |  " + self.fileName
		source = ttk.Label(header, text=sourceText, style="Meta.TLabel")
		source.grid(row=1, column=0, sticky=W, pady=(3, 0))

		videoFrame = Frame(self.master, bg="#0f172a", bd=0, highlightthickness=1, highlightbackground="#cbd5e1")
		videoFrame.grid(row=1, column=0, sticky=N+S+E+W, padx=24, pady=(0, 14))
		videoFrame.columnconfigure(0, weight=1)
		videoFrame.rowconfigure(0, weight=1)

		self.label = Label(
			videoFrame,
			bg="#0f172a",
			fg="#dbeafe",
			text="SETUP  ->  BUFFER  ->  PLAY",
			font=(self.fontFamily, 15, "bold"),
			height=19,
		)
		self.label.grid(row=0, column=0, sticky=N+S+E+W)
		self.label.bind("<Configure>", self.onVideoResize)

		panel = ttk.Frame(self.master, style="Panel.TFrame", padding=(18, 14, 18, 14))
		panel.grid(row=2, column=0, sticky=W+E, padx=24, pady=(0, 12))
		panel.columnconfigure(1, weight=0)
		panel.columnconfigure(3, weight=1)

		ttk.Label(panel, text="Quality", style="PanelTitle.TLabel").grid(row=0, column=0, sticky=W, padx=(0, 10), pady=(0, 12))
		self.quality = ttk.Combobox(
			panel,
			textvariable=self.qualityMode,
			values=("Auto", "SD", "720P", "1080P"),
			width=14,
			state="readonly",
			style="Quality.TCombobox",
			font=(self.fontFamily, 10),
		)
		self.quality.grid(row=0, column=1, sticky=W, pady=(0, 12))
		self.quality.bind("<<ComboboxSelected>>", lambda event: self.updateTransportPreview())

		self.transportLabel = ttk.Label(panel, text="Transport: " + self.selectedTransport(), style="PanelText.TLabel")
		self.transportLabel.grid(row=0, column=2, sticky=W, padx=(26, 10), pady=(0, 12))

		self.sessionLabel = ttk.Label(panel, text="Session: -", style="PanelText.TLabel")
		self.sessionLabel.grid(row=0, column=3, sticky=W, pady=(0, 12))

		self.bufferLabel = ttk.Label(panel, text="Buffer: 0/" + str(PREBUFFER_FRAMES), style="PanelTitle.TLabel")
		self.bufferLabel.grid(row=1, column=0, sticky=W, padx=(0, 8))

		self.bufferProgress = ttk.Progressbar(panel, maximum=PREBUFFER_FRAMES, value=0, style="Buffer.Horizontal.TProgressbar")
		self.bufferProgress.grid(row=1, column=1, columnspan=3, sticky=W+E)

		actions = ttk.Frame(self.master, style="App.TFrame", padding=(24, 0, 24, 14))
		actions.grid(row=3, column=0, sticky=W+E)
		for column in range(4):
			actions.columnconfigure(column, weight=1)

		self.setup = ttk.Button(actions, text="Setup", command=self.setupMovie, style="Primary.TButton")
		self.setup.grid(row=0, column=0, sticky=W+E, padx=(0, 8))

		self.start = ttk.Button(actions, text="Play", command=self.playMovie, style="Primary.TButton")
		self.start.grid(row=0, column=1, sticky=W+E, padx=8)

		self.pause = ttk.Button(actions, text="Pause", command=self.pauseMovie, style="Secondary.TButton")
		self.pause.grid(row=0, column=2, sticky=W+E, padx=8)

		self.teardown = ttk.Button(actions, text="Teardown", command=self.exitClient, style="Danger.TButton")
		self.teardown.grid(row=0, column=3, sticky=W+E, padx=(8, 0))

		self.statusLabel = ttk.Label(self.master, text="Ready to connect", style="Status.TLabel", padding=(16, 8))
		self.statusLabel.grid(row=4, column=0, sticky=W+E)

	def setupMovie(self):
		if self.state == self.INIT:
			self.setStatus("Sending SETUP...")
			self.sendRtspRequest(self.SETUP)

	def exitClient(self):
		self.receiverRunning = False
		self.setStatus("Closing session...")
		self.sendRtspRequest(self.TEARDOWN)
		self.master.destroy()
		try:
			os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
		except FileNotFoundError:
			pass

	def pauseMovie(self):
		if self.state == self.PLAYING:
			self.setStatus("Pausing stream...")
			self.sendRtspRequest(self.PAUSE)

	def playMovie(self):
		if self.state == self.READY:
			self.playEvent.clear()
			self.setStatus("Starting playback...")
			self.sendRtspRequest(self.PLAY)

	def playBufferedFrame(self):
		if self.state != self.PLAYING or self.playEvent.isSet():
			return

		frame = None
		with self.bufferLock:
			if self.frameBuffer:
				frame = self.frameBuffer.popleft()
			bufferSize = len(self.frameBuffer)
		self.updateBufferLabel(bufferSize)

		if frame is not None:
			self.updateMovie(self.writeFrame(frame))

		self.master.after(PLAYBACK_DELAY_MS, self.playBufferedFrame)

	def listenRtp(self):
		"""Listen for RTP packets over UDP and place complete frames in the jitter buffer."""
		while self.receiverRunning:
			try:
				data = self.rtpSocket.recv(20480)
				if data:
					self.handleRtpPacket(data)
			except socket.timeout:
				continue
			except OSError:
				break

	def handleRtpPacket(self, data):
		rtpPacket = RtpPacket()
		rtpPacket.decode(data)
		payload = rtpPacket.getPayload()

		if len(payload) < FRAGMENT_HEADER.size or payload[:2] != FRAGMENT_MAGIC:
			self.queueFrame(rtpPacket.seqNum(), payload)
			return

		_, frameNumber, fragmentIndex, fragmentCount = FRAGMENT_HEADER.unpack(payload[:FRAGMENT_HEADER.size])
		fragmentPayload = payload[FRAGMENT_HEADER.size:]
		parts = self.fragments.setdefault(frameNumber, {})
		parts[fragmentIndex] = fragmentPayload

		if len(parts) == fragmentCount:
			frame = b"".join(parts[index] for index in range(fragmentCount))
			del self.fragments[frameNumber]
			self.queueFrame(frameNumber, frame)

	def queueFrame(self, frameNumber, frame):
		if frameNumber <= self.frameNbr:
			return
		self.frameNbr = frameNumber
		with self.bufferLock:
			while len(self.frameBuffer) >= MAX_BUFFER_FRAMES:
				self.frameBuffer.popleft()
			self.frameBuffer.append(frame)
			bufferSize = len(self.frameBuffer)
		self.master.after(0, self.updateBufferLabel, bufferSize)
		print("Buffered frame: " + str(frameNumber))

	def updateBufferLabel(self, bufferSize=None):
		if bufferSize is None:
			with self.bufferLock:
				bufferSize = len(self.frameBuffer)
		self.bufferLabel.configure(text="Buffer: " + str(bufferSize) + "/" + str(PREBUFFER_FRAMES))
		self.bufferProgress.configure(value=min(bufferSize, PREBUFFER_FRAMES))

	def writeFrame(self, data):
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		file = open(cachename, "wb")
		file.write(data)
		file.close()
		return cachename

	def updateMovie(self, imageFile):
		self.currentImageFile = imageFile
		image = Image.open(imageFile)
		width = max(self.label.winfo_width(), 1)
		height = max(self.label.winfo_height(), 1)
		image = self.fitImageToPlayer(image, width, height)
		photo = ImageTk.PhotoImage(image)
		self.label.configure(image=photo, text="")
		self.label.image = photo

	def fitImageToPlayer(self, image, width, height):
		imageWidth, imageHeight = image.size
		if imageWidth <= 0 or imageHeight <= 0:
			return image

		scale = min(width / imageWidth, height / imageHeight)
		newWidth = max(1, int(imageWidth * scale))
		newHeight = max(1, int(imageHeight * scale))
		return image.resize((newWidth, newHeight), Image.LANCZOS)

	def onVideoResize(self, event):
		if self.currentImageFile is None:
			return
		if self.resizeJob is not None:
			self.master.after_cancel(self.resizeJob)
		self.resizeJob = self.master.after(80, self.redrawCurrentFrame)

	def redrawCurrentFrame(self):
		self.resizeJob = None
		if self.currentImageFile is not None:
			self.updateMovie(self.currentImageFile)

	def connectToServer(self):
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
			threading.Thread(target=self.recvRtspReply, daemon=True).start()
		except OSError:
			tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)

	def sendRtspRequest(self, requestCode):
		request = ''
		if requestCode == self.SETUP and self.state == self.INIT:
			self.transport = self.selectedTransport()
			self.rtspSeq += 1
			request = 'SETUP ' + self.fileName + ' RTSP/1.0\n'
			request += 'CSeq: ' + str(self.rtspSeq) + '\n'
			if self.transport == "TCP":
				request += 'Transport: RTP/TCP; interleaved=0-1'
			else:
				request += 'Transport: RTP/UDP; client_port=' + str(self.rtpPort)
			request += '\nQuality: ' + self.selectedQuality()
			self.requestSent = self.SETUP

		elif requestCode == self.PREFETCH and self.state == self.READY:
			self.rtspSeq += 1
			request = 'PREFETCH ' + self.fileName + ' RTSP/1.0\n'
			request += 'CSeq: ' + str(self.rtspSeq) + '\n'
			request += 'Session: ' + str(self.sessionId) + '\n'
			request += 'Frames: ' + str(PREBUFFER_FRAMES)
			self.requestSent = self.PREFETCH

		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq += 1
			request = 'PLAY ' + self.fileName + ' RTSP/1.0\n'
			request += 'CSeq: ' + str(self.rtspSeq) + '\n'
			request += 'Session: ' + str(self.sessionId)
			self.requestSent = self.PLAY

		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			self.rtspSeq += 1
			request = 'PAUSE ' + self.fileName + ' RTSP/1.0\n'
			request += 'CSeq: ' + str(self.rtspSeq) + '\n'
			request += 'Session: ' + str(self.sessionId)
			self.requestSent = self.PAUSE

		elif requestCode == self.TEARDOWN and self.state != self.INIT:
			self.rtspSeq += 1
			request = 'TEARDOWN ' + self.fileName + ' RTSP/1.0\n'
			request += 'CSeq: ' + str(self.rtspSeq) + '\n'
			request += 'Session: ' + str(self.sessionId)
			self.requestSent = self.TEARDOWN
		else:
			return

		self.pendingRequests[self.rtspSeq] = requestCode
		self.rtspSocket.send((request + '\n\n').encode())
		print('\nData sent:\n' + request)

	def recvRtspReply(self):
		"""Receive RTSP replies and interleaved TCP RTP packets on the control socket."""
		while self.receiverRunning:
			try:
				chunk = self.rtspSocket.recv(4096)
			except OSError:
				break
			if not chunk:
				break

			self.rtspBuffer += chunk
			while self.rtspBuffer:
				if self.rtspBuffer[0] == ord("$"):
					if len(self.rtspBuffer) < TCP_FRAME_HEADER.size:
						break
					_, _, packetLength = TCP_FRAME_HEADER.unpack(self.rtspBuffer[:TCP_FRAME_HEADER.size])
					totalLength = TCP_FRAME_HEADER.size + packetLength
					if len(self.rtspBuffer) < totalLength:
						break
					packet = self.rtspBuffer[TCP_FRAME_HEADER.size:totalLength]
					self.rtspBuffer = self.rtspBuffer[totalLength:]
					self.handleRtpPacket(packet)
				else:
					if b"\n\n" not in self.rtspBuffer:
						if self.tryParseLegacyRtspReply():
							continue
						break
					reply, self.rtspBuffer = self.rtspBuffer.split(b"\n\n", 1)
					if reply.strip():
						self.parseRtspReply(reply.decode("utf-8"))

	def tryParseLegacyRtspReply(self):
		parts = self.rtspBuffer.split(b"\n")
		if len(parts) < 3:
			return False
		if not parts[0].startswith(b"RTSP/1.0"):
			return False
		reply = b"\n".join(parts[:3])
		self.rtspBuffer = b"\n".join(parts[3:])
		self.parseRtspReply(reply.decode("utf-8"))
		return True

	def parseRtspReply(self, data):
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		session = int(lines[2].split(' ')[1])

		if self.sessionId == 0:
			self.sessionId = session

		if self.sessionId == session and int(lines[0].split(' ')[1]) == 200:
			requestCode = self.pendingRequests.pop(seqNum, None)
			transportLine = next((line for line in lines if line.startswith("Transport:")), "")
			if "RTP/TCP" in transportLine:
				self.transport = "TCP"
			elif "RTP/UDP" in transportLine:
				self.transport = "UDP"

			if requestCode == self.SETUP:
				self.state = self.READY
				self.master.after(0, self.sessionLabel.configure, {"text": "Session: " + str(self.sessionId)})
				if self.transport == "UDP":
					self.openRtpPort()
				self.setStatus("SETUP complete. Pre-buffering frames...")
				self.sendRtspRequest(self.PREFETCH)
			elif requestCode == self.PLAY:
				self.state = self.PLAYING
				self.setStatus("Playing from client buffer")
				self.master.after(0, self.playBufferedFrame)
			elif requestCode == self.PAUSE:
				self.state = self.READY
				self.playEvent.set()
				self.setStatus("Paused")
			elif requestCode == self.TEARDOWN:
				self.state = self.INIT
				self.teardownAcked = 1
				self.receiverRunning = False
				self.setStatus("Session closed")
			elif requestCode == self.PREFETCH:
				self.setStatus("Pre-buffer ready. Press Play.")

			self.master.after(0, self.updateControls)

	def openRtpPort(self):
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.rtpSocket.settimeout(0.5)

		try:
			self.rtpSocket.bind(('', self.rtpPort))
			if not self.rtpListenerStarted:
				self.rtpListenerStarted = True
				threading.Thread(target=self.listenRtp, daemon=True).start()
		except OSError:
			tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)

	def isHdStream(self, filename):
		lowerName = filename.lower()
		return "720" in lowerName or "1080" in lowerName

	def updateControls(self):
		self.setup.configure(state=NORMAL if self.state == self.INIT else DISABLED)
		self.start.configure(state=NORMAL if self.state == self.READY else DISABLED)
		self.pause.configure(state=NORMAL if self.state == self.PLAYING else DISABLED)
		self.teardown.configure(state=NORMAL if self.state != self.INIT else DISABLED)
		self.quality.configure(state="readonly" if self.state == self.INIT else DISABLED)
		self.updateTransportPreview()

	def updateTransportPreview(self):
		self.transportLabel.configure(text="Transport: " + self.selectedTransport())

	def setStatus(self, text):
		if hasattr(self, "statusLabel"):
			self.master.after(0, self.statusLabel.configure, {"text": text})

	def selectedQuality(self):
		mode = self.qualityMode.get().upper()
		if mode in ("720P", "1080P"):
			return mode
		if mode == "SD":
			return "SD"
		return "HD" if self.isHdStream(self.fileName) else "SD"

	def selectedTransport(self):
		return "TCP" if self.selectedQuality() in ("720P", "1080P", "HD") else "UDP"

	def preferredFont(self):
		families = set(tkFont.families(self.master))
		if "Montserrat" in families:
			return "Montserrat"
		if "Montserrat SemiBold" in families:
			return "Montserrat SemiBold"
		return "Segoe UI"

	def handler(self):
		self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else:
			self.playMovie()
