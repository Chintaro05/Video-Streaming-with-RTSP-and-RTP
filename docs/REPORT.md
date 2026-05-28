# Video Streaming with RTSP and RTP - Report

## 1. RTSP, RTP and UDP Fragmentation

The client implements the RTSP control flow with `SETUP`, `PLAY`, `PAUSE`, `TEARDOWN`, and an extra `PREFETCH` request for caching. Every RTSP request includes `CSeq`, and all session requests include the session id returned by the server.

The server packetizes MJPEG frames into RTP packets with RTP version 2, payload type 26, sequence number, timestamp, SSRC, and the 12-byte RTP header.

To avoid UDP packets larger than the network MTU, each video frame is split into fragments before sending. Each RTP payload starts with a small fragment header:

- magic bytes: `FR`
- frame number
- fragment index
- total fragment count

The client collects all fragments with the same frame number and reassembles them before displaying the frame.

## 2. I/O Multiplexing

The server no longer creates one thread per RTSP client. `Server.py` uses `select.select()` to monitor the listening socket and all connected RTSP sockets. Each `ServerWorker` stores its own state and is called by the event loop when data is available or when a frame is due to be sent.

## 3. HD Video Streaming with TCP

The client UI supports `Auto`, `SD`, `720P`, and `1080P`.

- `SD` uses RTP over UDP.
- `720P` and `1080P` use RTP over TCP.
- `Auto` uses TCP when the filename contains `720` or `1080`; otherwise it uses UDP.

For TCP streaming, RTP packets are interleaved on the RTSP TCP connection using a `$` header with channel and packet length. This avoids image corruption caused by UDP loss during HD streaming.

## 4. Client-Side Caching

After `SETUP`, the client automatically sends `PREFETCH`. The server then sends 20 frames ahead while the client is still in the ready state.

The client stores completed frames in a jitter buffer. When the user clicks `PLAY`, playback reads frames from the buffer at a fixed interval, which reduces visible jitter caused by network delay.

## 5. How to Run

Start the server:

```bash
python Server.py 8554
```

Start the client:

```bash
python ClientLauncher.py 127.0.0.1 8554 25000 movie.Mjpeg
```

For SD, choose `SD` or `Auto` with `movie.Mjpeg`.

For HD behavior, choose `720P` or `1080P` in the client before pressing `SETUP`. If an HD MJPEG file is available, it can also be requested by filename, for example:

```bash
python ClientLauncher.py 127.0.0.1 8554 25000 movie_720.Mjpeg
```
