"""Account PKCS12 decoding into private immutable file generations.

Password resolution is explicit; no bundled password, key or cloud request.
Old generations are retained for concurrent readers. The owner of the session
store must perform garbage collection only after retiring all references.
"""
import base64
import os
import shutil
import tempfile
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from .certificate_validation import certificate_usable
from .errors import ValidationError


class AccountMaterialUnavailable(RuntimeError):
    def __init__(self):super().__init__('Account certificate material unavailable')


class AccountMaterialProvider:
    def __init__(self,root,password_candidates):
        if not isinstance(root,Path) or not callable(password_candidates):
            raise ValidationError('Explicit private directory and password resolver required')
        self.root,self.resolve=root,password_candidates

    def __call__(self,data):
        generation=None
        try:
            value=data.get('base64Cert')
            if not isinstance(value,str) or not 0<len(value)<=1400000:raise ValueError()
            bundle=base64.b64decode(value,validate=True)
            if len(bundle)>1024*1024:raise ValueError()
            pair=None
            for index,password in enumerate(self.resolve(data)):
                if index>=8:break
                if not isinstance(password,bytes) or len(password)>4096:raise ValueError()
                try:
                    key,cert,chain=pkcs12.load_key_and_certificates(bundle,password)
                    if key is None or cert is None:continue
                    pair=key,cert,chain;break
                except (ValueError,TypeError):continue
            if pair is None:raise ValueError()
            key,cert,chain=pair
            from .private_storage import ensure_private_directory
            ensure_private_directory(self.root)
            generation=Path(tempfile.mkdtemp(prefix='generation-',dir=self.root))
            ensure_private_directory(generation)
            paths=generation/'cert.pem',generation/'key.pem'
            cert_pem=cert.public_bytes(serialization.Encoding.PEM)
            for extra in chain or ():cert_pem+=extra.public_bytes(serialization.Encoding.PEM)
            key_pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
            for path,content in zip(paths,(cert_pem,key_pem)):
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as stream:stream.write(content)
            if not certificate_usable(*paths):raise ValueError()
            return paths
        except Exception:
            if generation is not None:shutil.rmtree(generation,ignore_errors=True)
            raise AccountMaterialUnavailable() from None
