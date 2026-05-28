from random import randint
import math
import socket
import struct
from time import time

from VideoStream import VideoStream
from RtpPacket import RtpPacket

RTP_PAYLOAD_MTU = 1300
FRAGMENT_HEADER = struct.Struct("!2sHHH")
FRAGMENT_MAGIC = b"FR"
TCP_INTERLEAVED_CHANNEL = 0
FRAME_INTERVAL = 0.05
PREFETCH_FRAMES = 20


class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	PREFETCH = 'PREFETCH'

	INIT = 0
	READY = 1
	PLAYING = 2

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2

	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		self.state = self.INIT
		self.rtspBuffer = b""
		self.nextSendAt = 0
		self.prefetchRemaining = 0
		self.transport = "UDP"

	def recvRtspRequest(self):
		"""Receive RTSP request from the client without dedicating a thread."""
		connSocket = self.clientInfo['rtspSocket'][0]
		try:
			data = connSocket.recv(4096)
		except BlockingIOError:
			return True
		except OSError:
			return False

		if not data:
			return False

		self.rtspBuffer += data
		while True:
			rawRequest = self.popRtspRequest()
			if rawRequest is None:
				break
			if not rawRequest:
				continue
			requestText = rawRequest.decode("utf-8")
			print("Data received:\n" + requestText)
			self.processRtspRequest(requestText)
		return True

	def popRtspRequest(self):
		if b"\n\n" in self.rtspBuffer:
			rawRequest, self.rtspBuffer = self.rtspBuffer.split(b"\n\n", 1)
			return rawRequest.strip()

		lines = self.rtspBuffer.split(b"\n")
		if len(lines) < 3:
			return None

		method = lines[0].split(b" ", 1)[0]
		requiredLines = 4 if method == b"PREFETCH" else 3
		if len(lines) < requiredLines:
			return None

		rawRequest = b"\n".join(lines[:requiredLines]).strip()
		self.rtspBuffer = b"\n".join(lines[requiredLines:])
		return rawRequest

	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		filename = line1[1]
		seq = request[1].split(' ')

		if requestType == self.SETUP:
			if self.state == self.INIT:
				print("processing SETUP\n")
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
					return

				self.clientInfo['session'] = randint(100000, 999999)
				self.transport = self.requestedTransport(request, filename)
				if self.transport == "UDP":
					self.clientInfo['rtpPort'] = self.parseClientPort(request)
					self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				self.replyRtsp(self.OK_200, seq[1])

		elif requestType == self.PREFETCH:
			if self.state == self.READY:
				print("processing PREFETCH\n")
				self.prefetchRemaining = max(self.prefetchRemaining, PREFETCH_FRAMES)
				self.nextSendAt = 0
				self.replyRtsp(self.OK_200, seq[1])

		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				self.nextSendAt = 0
				self.replyRtsp(self.OK_200, seq[1])

		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				self.replyRtsp(self.OK_200, seq[1])

		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")
			self.replyRtsp(self.OK_200, seq[1])
			if self.transport == "UDP" and 'rtpSocket' in self.clientInfo:
				self.clientInfo['rtpSocket'].close()
			self.state = self.INIT

	def requestedTransport(self, request, filename):
		transportLine = next((line for line in request if line.startswith("Transport:")), "")
		qualityLine = next((line for line in request if line.startswith("Quality:")), "")
		lowerName = filename.lower()
		isHd = "720" in lowerName or "1080" in lowerName or "720" in qualityLine or "1080" in qualityLine or "HD" in qualityLine
		if "RTP/TCP" in transportLine or isHd:
			return "TCP"
		return "UDP"

	def parseClientPort(self, request):
		transportLine = next((line for line in request if line.startswith("Transport:")), "")
		if "client_port=" in transportLine:
			return transportLine.split("client_port=", 1)[1].split(";")[0].strip()
		return "25000"

	def send_due_frames(self):
		if self.state not in (self.READY, self.PLAYING):
			return
		if self.state != self.PLAYING and self.prefetchRemaining <= 0:
			return
		if time() < self.nextSendAt:
			return

		if self.send_next_frame() and self.prefetchRemaining > 0:
			self.prefetchRemaining -= 1
		self.nextSendAt = time() + FRAME_INTERVAL

	def send_next_frame(self):
		data = self.clientInfo['videoStream'].nextFrame()
		if not data:
			return False

		frameNumber = self.clientInfo['videoStream'].frameNbr()
		try:
			for packet in self.makeRtpPackets(data, frameNumber):
				if self.transport == "TCP":
					self.sendTcpRtp(packet)
				else:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])
					self.clientInfo['rtpSocket'].sendto(packet, (address, port))
			return True
		except OSError:
			print("Connection Error")
			return False

	def makeRtpPackets(self, payload, frameNbr):
		"""RTP-packetize and fragment video data so each UDP datagram stays below MTU."""
		maxPayload = RTP_PAYLOAD_MTU - FRAGMENT_HEADER.size
		fragmentCount = max(1, int(math.ceil(len(payload) / float(maxPayload))))
		packets = []

		for fragmentIndex in range(fragmentCount):
			start = fragmentIndex * maxPayload
			fragmentPayload = payload[start:start + maxPayload]
			fragmentHeader = FRAGMENT_HEADER.pack(
				FRAGMENT_MAGIC,
				frameNbr & 0xFFFF,
				fragmentIndex,
				fragmentCount,
			)
			rtpPacket = RtpPacket()
			rtpPacket.encode(2, 0, 0, 0, frameNbr, 0, 26, 0, fragmentHeader + fragmentPayload)
			packets.append(rtpPacket.getPacket())

		return packets

	def sendTcpRtp(self, packet):
		connSocket = self.clientInfo['rtspSocket'][0]
		header = struct.pack("!BBH", ord("$"), TCP_INTERLEAVED_CHANNEL, len(packet))
		connSocket.sendall(header + packet)

	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			reply = 'RTSP/1.0 200 OK\n'
			reply += 'CSeq: ' + seq + '\n'
			reply += 'Session: ' + str(self.clientInfo['session']) + '\n'
			reply += 'Transport: RTP/' + self.transport + '\n\n'
			self.clientInfo['rtspSocket'][0].send(reply.encode())
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")
