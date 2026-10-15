"""Creates the main PC's self-signed TLS certificate (M0.6).

Office PCs do not trust it through a certificate authority: they pin its fingerprint while pairing. Usage:
``python -m app.scripts.make_cert <out_dir> <server name> [ip ...]`` → ``server.crt`` and ``server.key``.
"""

import ipaddress
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 3650


def make_cert(out_dir: Path, server_name: str, ips: list[str]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, server_name[:64])])
    alt_names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    alt_names += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ["127.0.0.1", *ips]]
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = out_dir / "server.crt", out_dir / "server.key"
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_file, key_file


if __name__ == "__main__":
    make_cert(Path(sys.argv[1]), sys.argv[2], sys.argv[3:])
