#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地限流 mock（多线程）：活跃并发超过 MAX_ACTIVE 立即返回 503，制造击穿点。"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_ACTIVE = 20   # 同时最多处理 20 个活跃请求，超出即过载 503
_lock = threading.Lock()
_active = 0

class LimitedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        global _active
        with _lock:
            _active += 1
            overloaded = _active > MAX_ACTIVE
        try:
            time.sleep(0.02)  # 模拟处理耗时
            if overloaded:
                body = b'{"error":"overloaded"}'
                self.send_response(503)
            else:
                body = b'{"status":"ok"}'
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with _lock:
                _active -= 1
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    print("threaded limited mock on 8099 (max concurrency 20)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8099), LimitedHandler).serve_forever()
