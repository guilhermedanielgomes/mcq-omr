"""Start the MCQ-OMR server.   python run.py [port]

Prints the address to open on this computer and the one to type into the phone app.
"""
import socket
import sys

import uvicorn


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # nothing is sent (UDP connect); this only picks the network interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print("=" * 60)
    print(f"  On this computer:   http://localhost:{port}")
    print(f"  Phone app server:   http://{lan_ip()}:{port}")
    print("  (phone and computer must be on the same network, or use Tailscale)")
    print("=" * 60)
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
