"""Start the MCQ-OMR server.   python run.py [port]

Prints the address to open on this computer and the one to type into the phone app.
"""
import os
import sys

import uvicorn

from omr.network import lan_ip


def phone_password() -> str:
    """The password other devices need (Settings > Network), or "" when none is needed."""
    if os.environ.get("MCQ_TOKEN", "").strip():
        return ""
    from omr import db
    db.init()
    st = db.settings_get()
    return str(st.get("lan_password") or "") if st.get("lan_protect") else ""


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    password = phone_password()
    print("=" * 60)
    print(f"  On this computer:   http://localhost:{port}")
    print(f"  Phone app server:   http://{lan_ip()}:{port}")
    if password:
        print(f"  Phone password:     {password}   (Settings > Network)")
    print("  (phone and computer must be on the same network, or use Tailscale)")
    print("=" * 60)
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
