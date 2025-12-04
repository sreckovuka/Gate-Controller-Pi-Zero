# BlynkLib.py (patched for Python, Pi Zero safe reconnect/backoff)
__version__ = "1.0.1"

import struct
import time
import sys
import socket

try:
    import machine
    gettime = lambda: time.ticks_ms()
    SOCK_TIMEOUT = 0
except ImportError:
    const = lambda x: x
    gettime = lambda: int(time.time() * 1000)
    SOCK_TIMEOUT = 1.0  # non-MicroPython safe timeout

def dummy(*args):
    pass

MSG_RSP = const(0)
MSG_LOGIN = const(2)
MSG_PING  = const(6)
MSG_HW = const(20)
MSG_HW_LOGIN = const(29)
MSG_EVENT_LOG = const(64)
MSG_INTERNAL = const(17)
MSG_HW_SYNC = const(16)
MSG_PROPERTY = const(19)
MSG_BRIDGE = const(15)
MSG_REDIRECT  = const(41)

STA_SUCCESS = const(200)
STA_INVALID_TOKEN = const(9)

DISCONNECTED = const(0)
CONNECTING = const(1)
CONNECTED = const(2)

class EventEmitter:
    def __init__(self):
        self._cbks = {}
    def on(self, evt, f=None):
        if f: self._cbks[evt] = f
        else:
            def D(f):
                self._cbks[evt] = f
                return f
            return D
    def emit(self, evt, *a, **kv):
        if evt in self._cbks:
            try:
                self._cbks[evt](*a, **kv)
            except Exception as e:
                print(f"[BLYNK EMIT ERROR] {evt}: {e}")

class BlynkProtocol(EventEmitter):
    def __init__(self, auth, heartbeat=50, buffin=1024, log=None):
        EventEmitter.__init__(self)
        self.heartbeat = heartbeat*1000
        self.buffin = buffin
        self.log = log or dummy
        self.auth = auth
        self.state = DISCONNECTED
        self.bin = b""
        self.connect()

    def virtual_write(self, pin, *val):
        self._send(MSG_HW, 'vw', pin, *val)
    def log_event(self, *val):
        self._send(MSG_EVENT_LOG, *val)
    def _send(self, cmd, *args, **kwargs):
        if 'id' in kwargs:
            mid = kwargs.get('id')
        else:
            mid = getattr(self, 'msg_id', 1)
            self.msg_id = mid + 1 if mid < 0xFFFF else 1
        if cmd == MSG_RSP:
            data = b''
            dlen = args[0]
        else:
            data = ('\0'.join(map(str, args))).encode('utf8')
            dlen = len(data)
        msg = struct.pack("!BHH", cmd, mid, dlen) + data
        self.lastSend = gettime()
        self._write(msg)

    def connect(self):
        if self.state != DISCONNECTED: return
        self.state = CONNECTING
        self.msg_id = 1
        self.lastRecv = self.lastSend = self.lastPing = gettime()
        self._send(MSG_HW_LOGIN, self.auth)
    def disconnect(self):
        self.state = DISCONNECTED
        try:
            self.conn.close()
        except Exception: pass
        self.emit('disconnected')

    def process(self, data=None):
        if self.state not in (CONNECTING, CONNECTED): return
        now = gettime()
        if now - self.lastRecv > self.heartbeat+(self.heartbeat//2):
            return self.disconnect()
        if now - self.lastPing > self.heartbeat//10 and (now - self.lastSend > self.heartbeat or now - self.lastRecv > self.heartbeat):
            self._send(MSG_PING)
            self.lastPing = now
        if data: self.bin += data
        while len(self.bin) >= 5:
            cmd, mid, dlen = struct.unpack("!BHH", self.bin[:5])
            if mid == 0: return self.disconnect()
            if len(self.bin) < 5+dlen: break
            payload = self.bin[5:5+dlen]
            self.bin = self.bin[5+dlen:]
            args = list(map(lambda x: x.decode('utf8'), payload.split(b'\0')))
            if cmd == MSG_PING:
                self._send(MSG_RSP, STA_SUCCESS, id=mid)
            elif cmd == MSG_HW:
                if args[0] == 'vw':
                    self.emit("V"+args[1], args[2:])
                    self.emit("V*", args[1], args[2:])
            elif cmd == MSG_INTERNAL:
                self.emit("internal:"+args[0], args[1:])
            elif cmd == MSG_REDIRECT:
                self.emit("redirect", args[0], int(args[1]))
            elif cmd == MSG_RSP and self.state == CONNECTING:
                if dlen == STA_SUCCESS:
                    self.state = CONNECTED
                    self.emit('connected')
                elif dlen == STA_INVALID_TOKEN:
                    self.emit("invalid_auth")
                    print("Invalid auth token")
                    self.disconnect()

class Blynk(BlynkProtocol):
    def __init__(self, auth, server='blynk.cloud', port=443, insecure=False, **kwargs):
        self.server = server
        self.port = port
        self.insecure = insecure
        self.sock = None
        self.backoff = 1
        super().__init__(auth, **kwargs)
        self.on('redirect', self.redirect)

    def redirect(self, server, port):
        self.server = server
        self.port = port
        self.disconnect()
        self.connect()

    def connect(self):
        while True:
            try:
                s = socket.socket()
                s.settimeout(SOCK_TIMEOUT)
                s.connect(socket.getaddrinfo(self.server, self.port)[0][-1])
                if self.insecure:
                    self.conn = s
                else:
                    import ssl
                    self.conn = ssl.create_default_context().wrap_socket(s, server_hostname=self.server)
                super().connect()
                self.backoff = 1
                return
            except Exception as e:
                print(f"[BLYNK CONNECT ERROR] {e}, retry in {self.backoff}s")
                try: s.close()
                except: pass
                time.sleep(self.backoff)
                self.backoff = min(self.backoff*2, 30)

    def _write(self, data):
        try:
            self.conn.write(data)
        except Exception as e:
            print(f"[BLYNK WRITE ERROR] {e}")
            self.disconnect()

    def run(self):
        try:
            data = b''
            try:
                data = self.conn.read(self.buffin)
            except socket.timeout:
                time.sleep(0.05)
            except Exception:
                self.disconnect()
                time.sleep(0.5)
                return
            self.process(data)
        except Exception as e:
            print(f"[BLYNK RUN ERROR] {e}")
            self.disconnect()
            time.sleep(0.5)
