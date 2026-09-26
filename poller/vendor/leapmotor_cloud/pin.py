"""Token-bound PIN encryption; deliberately no static-key fallback."""
import base64
import hashlib
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from .errors import ValidationError


def encrypt_operate_password(pin,token):
    if (not isinstance(pin,str) or not pin.isascii() or not pin.isdecimal()
            or not 1<=len(pin)<=32):
        raise ValidationError('Invalid vehicle PIN')
    if not isinstance(token,str) or not 64<=len(token)<=16384 or not token.isascii():
        raise ValidationError('A valid session token is required for PIN encryption')
    key=hashlib.md5(token[:32].encode()).hexdigest()[8:24].encode()
    iv=hashlib.md5(token[32:64].encode()).hexdigest()[8:24].encode()
    padder=padding.PKCS7(128).padder()
    clear=padder.update(pin.encode())+padder.finalize()
    enc=Cipher(algorithms.AES(key),modes.CBC(iv)).encryptor()
    return base64.b64encode(enc.update(clear)+enc.finalize()).decode('ascii')
