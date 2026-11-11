"""Create one persistent, git-ignored Android signing identity; never print its password."""
from pathlib import Path
import json
import os
import secrets
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
directory=ROOT/'.private/android-signing';directory.mkdir(parents=True,exist_ok=True)
key=directory/'qingdan-release.p12';credentials=directory/'credentials.json'
if key.exists() or credentials.exists():
    if not (key.exists() and credentials.exists()):raise SystemExit('Incomplete signing identity; inspect existing files before continuing')
else:
    password=secrets.token_urlsafe(32)
    script=r'''
$ErrorActionPreference='Stop'
$rsa=[Security.Cryptography.RSACng]::new(3072)
try {
 $request=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Qingdan Android Release',$rsa,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
 $certificate=$request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),[DateTimeOffset]::UtcNow.AddYears(30))
 $certificate.FriendlyName='qingdan'
 [IO.File]::WriteAllBytes($env:QD_SIGNING_PATH,$certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pkcs12,$env:QD_SIGNING_PASSWORD))
} finally {$rsa.Dispose()}
'''
    exe=Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',script],env={**os.environ,'QD_SIGNING_PATH':str(key),'QD_SIGNING_PASSWORD':password},capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise SystemExit('Could not create local Android signing identity')
    credentials.write_text(json.dumps({'ANDROID_STORE_PASSWORD':password,'ANDROID_KEY_ALIAS':'qingdan','ANDROID_KEY_PASSWORD':password}),encoding='utf-8')
    for path in (key,credentials):
        try:path.chmod(0o600)
        except OSError:pass
backup=directory/'qingdan-signing-backup.zip'
with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as archive:
    archive.write(key,key.name);archive.write(credentials,credentials.name)
print('Persistent signing identity ready; private backup:',backup)
