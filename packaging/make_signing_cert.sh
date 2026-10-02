#!/bin/sh
# Create a stable, self-signed code-signing certificate for RylanFlow.app and import it into
# the login keychain. Signing with a real certificate (instead of ad-hoc `codesign -s -`) keeps
# the app's signing identity the same across rebuilds, so macOS Accessibility/Input Monitoring
# grants survive a rebuild instead of resetting every time (ad-hoc signatures are keyed to the
# binary's own hash, which changes on every build; a certificate-backed signature is keyed to
# the certificate instead).
#
# The certificate does not need to be *trusted* by macOS (that's only for Gatekeeper, which
# the app already works around with right-click > Open) -- `codesign` can sign with it either
# way, which is all we need here.
#
# Run once: sh packaging/make_signing_cert.sh
# Safe to re-run: it skips creation if the identity already exists.
set -e

IDENTITY="RylanFlow Local Signing"
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

if security find-certificate -c "$IDENTITY" >/dev/null 2>&1; then
    echo "Signing identity '$IDENTITY' already exists. Nothing to do."
    exit 0
fi

echo "Creating a self-signed code-signing certificate named '$IDENTITY'..."
openssl req -x509 -newkey rsa:2048 -keyout "$WORKDIR/key.pem" -out "$WORKDIR/cert.pem" \
    -days 3650 -nodes -subj "/CN=$IDENTITY" \
    -addext "extendedKeyUsage=codeSigning" -addext "keyUsage=digitalSignature"

# Package into a .p12 so `security import` can bring in the certificate and its private key
# together. macOS's PKCS12 importer can't read OpenSSL 3's default cipher (AES-256 + SHA-256
# MAC), so -legacy asks for the older RC2/3DES/SHA-1 encoding it expects. An empty export
# password also trips up macOS's importer ("MAC verification failed"), so we use a fixed
# placeholder password instead; it never leaves this machine's login keychain.
openssl pkcs12 -export -legacy -out "$WORKDIR/cert.p12" \
    -inkey "$WORKDIR/key.pem" -in "$WORKDIR/cert.pem" -passout pass:rylanflow-local

echo "Importing into the login keychain..."
# -A grants every local app access to the private key without a per-use keychain prompt. This
# is a throwaway local-only identity (not a real Developer ID), so that trade-off is fine here.
# Deliberately not passing -k: an explicit keychain path here has been observed to silently
# fail the import on this machine, while importing into the default keychain works reliably.
security import "$WORKDIR/cert.p12" -P "rylanflow-local" -T /usr/bin/codesign -A

echo "Done. Verify with: security find-certificate -c \"$IDENTITY\""
