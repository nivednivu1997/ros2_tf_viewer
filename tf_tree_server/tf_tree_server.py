#!/usr/bin/env python3
"""
tf_tree_server.py - Real-time tf tree viewer in browser.

Bypasses tf2_ros.Buffer to avoid cache stickiness:
  - /tf edges expire if not seen for DYN_TIMEOUT_SEC
  - /tf_static is re-subscribed every STATIC_REFRESH_SEC so latched messages
    only get re-delivered by currently-alive publishers; dead static
    publishers drop out

Requirements: sudo apt install graphviz

Run on robot (sourced ROS2 env):
    python3 tf_tree_server.py
Then: http://<robot-ip>:8000  (or http://localhost:8000 if testing locally)
"""
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, HistoryPolicy
from tf2_msgs.msg import TFMessage
from collections import deque
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8000
DYN_TIMEOUT_SEC = 0.5       # drop dynamic edges not seen in this window
STATIC_REFRESH_SEC = 1.0    # re-subscribe /tf_static this often
WARMUP_SEC = 1.5


class TFCollector(Node):
    def __init__(self):
        super().__init__('tf_tree_server')
        # (parent, child) -> {'last_ns', 'is_static', 'times': deque}
        self._edges = {}
        self._lock = threading.Lock()

        self.create_subscription(TFMessage, '/tf', self._on_tf, 100)

        self._static_qos = QoSProfile(
            depth=100,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
        )
        self._static_sub = self.create_subscription(
            TFMessage, '/tf_static', self._on_tf_static, self._static_qos
        )
        self.create_timer(STATIC_REFRESH_SEC, self._refresh_static)

    def _record(self, transforms, is_static):
        now = self.get_clock().now().nanoseconds
        with self._lock:
            for t in transforms:
                key = (t.header.frame_id, t.child_frame_id)
                e = self._edges.get(key)
                if e is None:
                    e = {'is_static': is_static, 'times': deque(maxlen=30)}
                    self._edges[key] = e
                e['last_ns'] = now
                e['is_static'] = is_static
                e['times'].append(now)

    def _on_tf(self, msg):
        self._record(msg.transforms, is_static=False)

    def _on_tf_static(self, msg):
        self._record(msg.transforms, is_static=True)

    def _refresh_static(self):
        """Drop static edges and resubscribe; only alive publishers repopulate."""
        with self._lock:
            self._edges = {k: v for k, v in self._edges.items() if not v['is_static']}
        self.destroy_subscription(self._static_sub)
        self._static_sub = self.create_subscription(
            TFMessage, '/tf_static', self._on_tf_static, self._static_qos
        )

    def active_edges(self):
        now = self.get_clock().now().nanoseconds
        timeout_ns = int(DYN_TIMEOUT_SEC * 1e9)
        out = {}
        with self._lock:
            for k, v in self._edges.items():
                if not v['is_static'] and (now - v['last_ns']) > timeout_ns:
                    continue
                times = list(v['times'])
                rate = 0.0
                if len(times) > 1:
                    span = (times[-1] - times[0]) / 1e9
                    if span > 0:
                        rate = (len(times) - 1) / span
                out[k] = {
                    'is_static': v['is_static'],
                    'rate': rate,
                    'age_ms': (now - v['last_ns']) / 1e6,
                }
        return out

    def generate_svg(self):
        edges = self.active_edges()
        if not edges:
            return '<p>No tf frames currently being published.</p>'

        lines = [
            'digraph G {',
            '  rankdir=TB;',
            '  node [shape=ellipse, style=filled, fillcolor="#f0f0f0", fontname="Helvetica"];',
            '  edge [fontname="Helvetica", fontsize=9];',
        ]
        children, parents = set(), set()
        for (parent, child), info in edges.items():
            children.add(child)
            parents.add(parent)
            if info['is_static']:
                label = f'static\\nage: {info["age_ms"]:.0f} ms'
                attrs = 'color="#888", style=dashed'
            else:
                label = f'{info["rate"]:.1f} Hz\\nage: {info["age_ms"]:.0f} ms'
                attrs = 'color="#000"'
            lines.append(f'  "{parent}" -> "{child}" [label="{label}", {attrs}];')
        for root in (parents - children):
            lines.append(f'  "{root}" [fillcolor="#a8e6a3"];')
        lines.append('}')
        dot = '\n'.join(lines)

        try:
            r = subprocess.run(['dot', '-Tsvg'], input=dot,
                               capture_output=True, text=True, check=True)
            return r.stdout
        except FileNotFoundError:
            return ('<p style="color:#c00">graphviz not installed. '
                    '<code>sudo apt install graphviz</code></p>')
        except subprocess.CalledProcessError as e:
            return f'<pre style="color:#c00">{e.stderr}</pre>'

    def edge_count(self):
        with self._lock:
            return len(self._edges)


HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>TF Tree</title>
<style>
  body {{ font-family:-apple-system,system-ui,sans-serif; background:#1e1e1e; color:#ddd; margin:0; padding:1rem; }}
  .bar {{ display:flex; gap:1rem; align-items:center; margin-bottom:1rem; }}
  button {{ padding:.5rem 1rem; background:#0066cc; color:#fff; border:none; border-radius:4px; cursor:pointer; font-size:14px; }}
  button:hover {{ background:#0080ff; }}
  label {{ font-size:14px; }}
  .wrap {{ background:#fff; padding:1rem; border-radius:6px; overflow:auto; }}
  .wrap svg {{ max-width:100%; height:auto; }}
  small {{ color:#888; }}
</style>
<script>
  async function refresh() {{
    try {{
      const r = await fetch('/');
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      document.querySelector('.wrap').innerHTML = doc.querySelector('.wrap').innerHTML;
      document.querySelector('small').textContent = doc.querySelector('small').textContent;
    }} catch (e) {{ console.error(e); }}
  }}
  function toggleAuto(cb) {{
    clearInterval(window._iv);
    if (cb.checked) window._iv = setInterval(refresh, 500);
  }}
</script>
</head>
<body>
  <div class="bar">
    <button onclick="refresh()">&#8635; Refresh</button>
    <label><input type="checkbox" onchange="toggleAuto(this)" checked> auto-refresh (0.5s)</label>
    <small>Generated {ts} &middot; {count} active edges</small>
  </div>
  <div class="wrap">{svg}</div>
  <script>toggleAuto(document.querySelector('input[type=checkbox]'));</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    collector = None

    def do_GET(self):
        if self.path not in ('/', '/index.html'):
            self.send_response(404); self.end_headers(); return
        svg = self.collector.generate_svg()
        body = HTML.format(
            svg=svg,
            ts=time.strftime('%H:%M:%S'),
            count=self.collector.edge_count(),
        ).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    rclpy.init()
    node = TFCollector()
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    print(f'Warming up {WARMUP_SEC}s...', flush=True)
    time.sleep(WARMUP_SEC)
    Handler.collector = node
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'tf tree at http://0.0.0.0:{PORT}  (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
