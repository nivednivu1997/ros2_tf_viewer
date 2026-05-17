# tf_tree_server

Real-time ROS 2 tf tree viewer in your browser. A drop-in replacement for `ros2 run tf2_ros view_frames` when working over SSH — no PDF generation, no `scp`, just open a URL.

## Why

`ros2 run tf2_ros view_frames` writes a PDF to the working directory. On a headless robot accessed via SSH, that means an `scp` round-trip every time you want to look at the tf tree. It also takes a snapshot at one moment in time, so frames that have died are still shown if they were ever cached.

This tool runs as a small HTTP server on the robot. From your host PC you open `http://<robot-ip>:8000` in a browser and see the tree update live. Dead publishers drop out within ~1 second.

## Features

- Live tf tree rendered as inline SVG in the browser
- Smooth auto-refresh (no full-page reload, no flash)
- Per-edge publish rate and message age
- Dynamic edges (`/tf`) expire shortly after publisher stops
- Static edges (`/tf_static`) drop out when their publisher dies (via periodic re-subscribe trick)
- Root frames highlighted green so disconnected sub-trees are obvious
- Single-file, ~180 lines of Python, no extra ROS 2 dependencies beyond `rclpy` and `tf2_msgs`

## Requirements

- ROS 2 (tested on Humble; should work on Iron/Jazzy)
- `graphviz` for SVG rendering:
  ```bash
  sudo apt install graphviz
  ```

## Usage

### On the robot

```bash
source /opt/ros/<distro>/setup.bash
python3 tf_tree_server.py
```

Output:
```
Warming up 1.5s...
tf tree at http://0.0.0.0:8000  (Ctrl+C to stop)
```

### From your host PC

Open a browser to:
```
http://<robot-ip>:8000
```

The tree refreshes automatically every 0.5 s. Toggle the checkbox to pause auto-refresh; the button does a manual refresh.

### Over an SSH tunnel (if port 8000 is firewalled)

```bash
ssh -L 8000:localhost:8000 user@robot
```
Then open `http://localhost:8000` on the host.

## Testing locally

You can run the server on your own machine without a robot. In one terminal:

```bash
source /opt/ros/<distro>/setup.bash
python3 tf_tree_server.py
```

In another terminal, publish some fake frames:

```bash
ros2 run tf2_ros static_transform_publisher 0 0 0 0 0 0 map odom &
ros2 run tf2_ros static_transform_publisher 1 0 0 0 0 0 odom base_link &
ros2 run tf2_ros static_transform_publisher 0.2 0 0.3 0 0 0 base_link laser &
ros2 run tf2_ros static_transform_publisher 0.1 0 0.5 0 0 0 base_link camera_link &
ros2 run tf2_ros static_transform_publisher 0.05 0 0 0 0 0 camera_link tag0 &
```

Open `http://localhost:8000`. To watch frames disappear in real time:

```bash
pkill -f static_transform_publisher
```

## Configuration

Tunables at the top of `tf_tree_server.py`:

| Constant | Default | Meaning |
|---|---|---|
| `PORT` | `8000` | HTTP port to serve on |
| `DYN_TIMEOUT_SEC` | `0.5` | Dynamic edges drop after this long without a message |
| `STATIC_REFRESH_SEC` | `1.0` | How often `/tf_static` is re-subscribed |
| `WARMUP_SEC` | `1.5` | Initial delay before serving requests |

To restrict the server to localhost only (e.g. behind an SSH tunnel), change `'0.0.0.0'` to `'127.0.0.1'` in the `HTTPServer(...)` call.

## How it works

Standard `view_frames` uses `tf2_ros.Buffer.all_frames_as_yaml()`, which reports any frame in the buffer cache — including frames whose publisher has long since died. That's fine for a one-shot snapshot but bad for live monitoring.

This script instead:

1. Subscribes directly to `/tf` and `/tf_static` (no `Buffer`)
2. Tracks each `(parent, child)` edge with last-seen timestamp and a sliding window of recent timestamps for rate estimation
3. Dynamic edges are filtered out on render if not updated within `DYN_TIMEOUT_SEC`
4. Because `/tf_static` is latched (`TRANSIENT_LOCAL` durability), killing a static publisher doesn't notify subscribers. To detect death anyway, the script destroys and recreates the `/tf_static` subscription every `STATIC_REFRESH_SEC`. Only currently-alive publishers re-deliver their latched messages to the new subscription, so dead ones naturally drop out.
5. On each HTTP request, the active edges are serialized to DOT and piped through `dot -Tsvg`; the resulting SVG is embedded inline in the response page.

A single background thread runs `rclpy.spin`. HTTP handlers read a thread-safe snapshot of the edge state.

## Troubleshooting

**Page shows "No tf frames currently being published"**
Nothing is publishing to `/tf` or `/tf_static`. Verify with:
```bash
ros2 topic hz /tf
ros2 topic echo /tf_static --once
```

**Killed a publisher but its frames still show**
If you backgrounded it with `&` and closed the terminal, bash may not have killed it on most distros. Check:
```bash
ros2 node list
pkill -f static_transform_publisher  # nuclear option
```

**`graphviz not installed` error in browser**
Install it: `sudo apt install graphviz`

**Can't reach the server from the host**
- Confirm the server is bound to `0.0.0.0` and not `127.0.0.1`
- Check the robot's firewall: `sudo ufw status`
- Try the SSH tunnel method above
- Confirm host and robot are on the same network: `ping <robot-ip>`

## License

MIT
