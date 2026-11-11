"""Generate device-local self-signed PEMs without installing a certificate store entry."""
import hashlib
import os
from pathlib import Path
import shutil
import ssl
import subprocess

from qingdan.core.context import ctx
from qingdan.core.registry import validation_error


def ensure_certificate(host):
    directory=Path(ctx.data_dir)/'tls';directory.mkdir(exist_ok=True)
    suffix=hashlib.sha256(host.encode()).hexdigest()[:12]
    cert,key=directory/f'cert-{suffix}.pem',directory/f'key-{suffix}.pem'
    if not (cert.is_file() and key.is_file()):
        if os.name=='nt':
            script=r'''
$ErrorActionPreference='Stop'
$rsa=[System.Security.Cryptography.RSACng]::new(2048)
try {
  $request=[System.Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Qingdan',$rsa,[System.Security.Cryptography.HashAlgorithmName]::SHA256,[System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
  $san=[System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
  $san.AddDnsName('localhost')
  $addresses=@('127.0.0.1','::1')
  if($env:QD_TLS_HOST -notin @('0.0.0.0','::')){$addresses+=$env:QD_TLS_HOST}
  try{$addresses += [Net.Dns]::GetHostAddresses([Environment]::MachineName) | ForEach-Object {$_.ToString()}}catch{}
  foreach($address in ($addresses | Select-Object -Unique)){$san.AddIpAddress([Net.IPAddress]::Parse($address))}
  $request.CertificateExtensions.Add($san.Build())
  $certificate=$request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5),[DateTimeOffset]::UtcNow.AddYears(2))
  function Pem($label,$bytes){return "-----BEGIN $label-----`n"+[Convert]::ToBase64String($bytes,[Base64FormattingOptions]::InsertLineBreaks)+"`n-----END $label-----`n"}
  [IO.File]::WriteAllText($env:QD_TLS_CERT,(Pem 'CERTIFICATE' $certificate.RawData),[Text.Encoding]::ASCII)
  [IO.File]::WriteAllText($env:QD_TLS_KEY,(Pem 'PRIVATE KEY' $rsa.Key.Export([System.Security.Cryptography.CngKeyBlobFormat]::Pkcs8PrivateBlob)),[Text.Encoding]::ASCII)
} finally {$rsa.Dispose()}
'''
            exe=Path(os.environ.get('WINDIR','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            result=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',script],
                env={**os.environ,'QD_TLS_CERT':str(cert),'QD_TLS_KEY':str(key),'QD_TLS_HOST':host},
                capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        else:
            exe=shutil.which('openssl')
            if not exe:raise validation_error('自动证书生成不可用，请选择 HTTP 或配置已有证书')
            names='DNS:localhost,IP:127.0.0.1,IP:::1'+(f',IP:{host}' if host not in {'0.0.0.0','::'} else '')
            result=subprocess.run([exe,'req','-x509','-newkey','rsa:2048','-nodes','-days','730','-keyout',str(key),'-out',str(cert),'-subj','/CN=Qingdan','-addext','subjectAltName='+names],capture_output=True,timeout=45)
        if result.returncode:
            cert.unlink(missing_ok=True);key.unlink(missing_ok=True)
            raise validation_error('自动生成证书失败，请选择 HTTP 或配置 PEM 文件')
        try:key.chmod(0o600)
        except OSError:pass
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(str(cert),str(key),password=lambda:'')
    return str(cert),str(key)
