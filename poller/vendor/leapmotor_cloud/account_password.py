"""PKCS12 password derivation with caller-provided application parameters.

No application round keys, fallback passwords or APK assets are distributed.
Parameters must come from a legitimately provisioned private installation.
"""
import base64
import hashlib
from .errors import ValidationError


class AccountPasswordResolver:
    def __init__(self, *, round_keys, sbox, password_candidates=()):
        if (len(round_keys)!=32 or any(type(v)is not int or not 0<=v<2**32 for v in round_keys)
            or len(sbox)!=256 or any(type(v)is not int or not 0<=v<256 for v in sbox)
            or len(set(sbox))!=256):
            raise ValidationError('Invalid private application parameters')
        if len(password_candidates)>6 or any(not isinstance(p,str) or len(p)>4096 for p in password_candidates):
            raise ValidationError('Invalid private password candidates')
        self._rounds,self._sbox=tuple(round_keys),tuple(sbox)
        self._candidates=tuple(password_candidates)

    def __repr__(self):return 'AccountPasswordResolver(<private>)'

    def _block(self, block):
        words=[int.from_bytes(block[i:i+4],'big') for i in range(0,16,4)]
        def rol(value,n):return ((value<<n)|(value>>(32-n)))&0xffffffff
        for key in self._rounds:
            t=words[1]^words[2]^words[3]^key
            b=sum(self._sbox[(t>>n)&255]<<n for n in (24,16,8,0))
            next_word=words[0]^b^rol(b,2)^rol(b,10)^rol(b,18)^rol(b,24)
            words=words[1:]+[next_word&0xffffffff]
        return b''.join(word.to_bytes(4,'big') for word in reversed(words))

    def derive(self, account_id, uid):
        if type(account_id) not in (str,int) or not isinstance(uid,str):
            raise ValidationError('Invalid account password inputs')
        identifier=str(account_id)
        if not identifier or not uid or not identifier.isascii() or not uid.isascii() or max(len(identifier),len(uid))>4096:
            raise ValidationError('Invalid account password inputs')
        digest=hashlib.md5(identifier.encode('ascii')).hexdigest()
        source=(digest+digest[::2]+uid[1::2]).encode('ascii')
        block=hashlib.sha256(source).digest()[:16]
        # Only the first encrypted block contributes to the 12-byte prefix.
        return base64.b64encode(self._block(block)[:12]).decode('ascii')[:15]

    def candidates(self,data,*,explicit=None):
        values=[]
        if explicit is not None:
            if not isinstance(explicit,str) or len(explicit)>4096:raise ValidationError('Invalid explicit password')
            values.append(explicit)
        account=data.get('accountId',data.get('id'))
        uid=data.get('uid')
        if account is not None and uid is not None:values.append(self.derive(account,str(uid)))
        values.extend(self._candidates)
        return tuple(dict.fromkeys(v.encode('utf-8') for v in values))
