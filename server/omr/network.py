"""Network helpers: this computer's address on the Wi-Fi, the password other devices need (see
Settings > Network), the phone pairing link and its QR code."""
from __future__ import annotations

import io
import ipaddress
import secrets
import socket
from urllib.parse import quote

# lowercase letters and digits without look-alikes (no 0/o, 1/l/i), easy to type on a phone
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def lan_ip() -> str:
    """The address of this computer on the local network (what the phone app connects to)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # nothing is sent (UDP connect); this only picks the network interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def new_password() -> str:
    """12 characters in groups of 4, e.g. 'k7mq-3xtd-p9ra'."""
    chars = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(12))
    return "-".join(chars[i:i + 4] for i in range(0, 12, 4))


def is_loopback(host: str | None) -> bool:
    """True for a connection from this computer: 127.x.x.x, ::1 or ::ffff:127.x.x.x."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def pair_url(server: str, password: str) -> str:
    """What the pairing QR code holds: mcqomr://pair?u=<server address>&t=<password>."""
    return f"mcqomr://pair?u={quote(server, safe='')}&t={quote(password, safe='')}"


def qr_png(text: str, box_size: int = 8) -> bytes:
    """A QR code as a PNG (greyscale, so every PNG reader, Tk too, can show it)."""
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").get_image().convert("L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
