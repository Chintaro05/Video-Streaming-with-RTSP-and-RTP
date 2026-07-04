class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		
		# Detect format: try to read the first 5 bytes
		header = self.file.read(5)
		self.file.seek(0)
		
		try:
			if len(header) == 5:
				int(header) # Try to parse as integer (e.g. b'06014')
				self.custom_format = True
			else:
				self.custom_format = False
		except ValueError:
			self.custom_format = False
			
		self.buffer = b''
		
	def nextFrame(self):
		"""Get next frame."""
		if self.custom_format:
			data = self.file.read(5) # Get the framelength from the first 5 bits
			if data: 
				try:
					framelength = int(data)
					# Read the current frame
					data = self.file.read(framelength)
					self.frameNum += 1
					return data
				except ValueError:
					pass
			return b''
		else:
			# Standard MJPEG format: scan for JPEG frames starting with \xff\xd8 and ending with \xff\xd9
			while True:
				start_idx = self.buffer.find(b'\xff\xd8')
				if start_idx != -1:
					if start_idx > 0:
						self.buffer = self.buffer[start_idx:]
						start_idx = 0
					
					end_idx = self.buffer.find(b'\xff\xd9', 2)
					if end_idx != -1:
						frame_data = self.buffer[:end_idx + 2]
						self.buffer = self.buffer[end_idx + 2:]
						self.frameNum += 1
						return frame_data
				
				chunk = self.file.read(1024 * 64)
				if not chunk:
					# End of file. Return whatever is left if it is a partial frame.
					if self.buffer.startswith(b'\xff\xd8') and len(self.buffer) > 2:
						frame_data = self.buffer
						self.buffer = b''
						self.frameNum += 1
						return frame_data
					return b''
				self.buffer += chunk
		
	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum
	
	