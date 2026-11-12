"""Normalize a .NET-exported PKCS12 alias without changing the signing key."""
import os
import re
import subprocess

path = os.environ['ANDROID_KEYSTORE_PATH']
expected = os.environ['ANDROID_KEY_ALIAS']
base = ['keytool', '-J-Duser.language=en', '-J-Duser.country=US']
store = ['-keystore', path, '-storetype', 'PKCS12', '-storepass:env', 'ANDROID_STORE_PASSWORD']
result = subprocess.run(base + ['-list', '-v'] + store, capture_output=True, text=True, check=True)
aliases = re.findall(r'^Alias name: (.+)$', result.stdout, re.MULTILINE)
if len(aliases) != 1 or result.stdout.count('Entry type: PrivateKeyEntry') != 1:
    raise SystemExit('Expected exactly one private signing key')
if aliases[0].strip() != expected:
    subprocess.run(base + ['-changealias', '-alias', aliases[0].strip(), '-destalias', expected,
                          '-keypass:env', 'ANDROID_KEY_PASSWORD'] + store,
                   capture_output=True, text=True, check=True)
subprocess.run(base + ['-list', '-alias', expected] + store,
               capture_output=True, text=True, check=True)
print('Persistent signing key validated; original certificate and private key preserved')
