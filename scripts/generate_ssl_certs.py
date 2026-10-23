"""
SSL Certificate Generator for SignalSync HTTPS Production/Staging Deployment.
Generates an RSA 2048-bit self-signed certificate and private key.
"""
import datetime
from pathlib import Path
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

SSL_DIR = Path(__file__).resolve().parent.parent / "nginx" / "ssl"
SSL_DIR.mkdir(parents=True, exist_ok=True)

CERT_FILE = SSL_DIR / "signalsync.crt"
KEY_FILE = SSL_DIR / "signalsync.key"


def generate_self_signed_cert():
    print(f"Generating SSL certificate and private key in {SSL_DIR}...")
    
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Subject & Issuer
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Maharashtra"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Pune"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Group SignalSync"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.DNSName("signalsync.local")]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    # Write private key
    with open(KEY_FILE, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    # Write certificate
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"[OK] Certificate generated: {CERT_FILE}")
    print(f"[OK] Private key generated: {KEY_FILE}")


if __name__ == "__main__":
    generate_self_signed_cert()
